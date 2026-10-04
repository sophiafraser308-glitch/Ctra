"""StrategyService: upload/validate/store (DB is authoritative, filesystem is a cache), versioning, activation, rollback."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.audit import AuditService
from app.config import Settings
from app.core.enums import Category, StrategyVersionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailed
from app.core.utils import sha256_hex, utcnow
from app.database import Database
from app.logging import get_logger
from app.models import Bot, Strategy, StrategyVersion, Trade
from app.schemas.strategy import bump_patch, version_key
from app.strategies.validator import ValidationReport, validate_source

log = get_logger(Category.STRATEGY)
ACTIVE_BOT_STATES = ("RUNNING", "STARTING", "PAUSED", "RESTARTING", "LOCKED", "ERROR", "CRASHED")


class StrategyService:
    def __init__(self, db: Database, settings: Settings, audit: AuditService) -> None:
        self.db, self.s, self.audit = db, settings, audit
        self.base = Path(settings.strategies_dir)

    def validate(self, source: str) -> ValidationReport:
        return validate_source(source, allowed_imports=self.s.strategy_allowed_imports,
                               allowed_dependencies=self.s.strategy_allowed_dependencies,
                               max_bytes=self.s.strategy_max_file_bytes, max_dependencies=self.s.strategy_max_dependencies)

    # ---- queries ------------------------------------------------------------------------
    async def list(self) -> list[Strategy]:
        async with self.db.session() as s:
            return list((await s.execute(select(Strategy).where(Strategy.deleted_at.is_(None)).order_by(Strategy.name))).scalars())

    async def get(self, strategy_id: str) -> Strategy:
        async with self.db.session() as s:
            st = await s.get(Strategy, strategy_id)
        if st is None or st.deleted_at is not None:
            raise NotFoundError("Strategy not found")
        return st

    async def versions(self, strategy_id: str) -> list[StrategyVersion]:
        async with self.db.session() as s:
            rows = (await s.execute(select(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id))).scalars().all()
        return sorted(rows, key=lambda v: version_key(v.version), reverse=True)

    async def get_version(self, version_id: str) -> StrategyVersion:
        async with self.db.session() as s:
            v = await s.get(StrategyVersion, version_id)
        if v is None:
            raise NotFoundError("Strategy version not found")
        return v

    async def active_version(self, strategy_id: str) -> StrategyVersion | None:
        st = await self.get(strategy_id)
        return await self.get_version(st.active_version_id) if st.active_version_id else None

    # ---- ingest -----------------------------------------------------------------------------
    async def ingest(self, raw: bytes, filename: str, user_id: int) -> tuple[ValidationReport, Strategy | None, StrategyVersion | None]:
        if not filename.lower().endswith(".py"):
            raise ValidationFailed("Only .py files are accepted")
        if len(raw) > self.s.strategy_max_file_bytes:
            raise ValidationFailed(f"File larger than {self.s.strategy_max_file_bytes} bytes")
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailed("File must be UTF-8 text") from exc
        report = self.validate(source)
        if not report.ok:
            await self.audit.record(action="STRATEGY_UPLOAD", user_id=user_id, target=filename, result="REJECTED", error="; ".join(report.errors)[:500], meta={"sha256": report.sha256})
            return report, None, None
        md = report.metadata
        async with self.db.session() as s:
            st = (await s.execute(select(Strategy).where(Strategy.name == md["name"]))).scalar_one_or_none()
            if st is not None and st.deleted_at is not None:
                st.deleted_at = None
            if st is None:
                st = Strategy(name=md["name"], description=md.get("description"), author=md.get("author"), created_by=user_id)
                s.add(st)
                await s.flush()
            existing = list((await s.execute(select(StrategyVersion).where(StrategyVersion.strategy_id == st.id))).scalars())
            if any(v.file_hash == report.sha256 for v in existing):
                report.ok = False
                report.errors.append("identical file already uploaded as a version of this strategy")
                return report, st, None
            taken = {v.version for v in existing}
            version = md.get("version")
            if not version or version in taken:
                latest = max((v.version for v in existing), key=version_key, default=None)
                version = bump_patch(latest) if latest else "1.0.0"
                if md.get("version"):
                    report.warnings.append(f"declared version {md['version']} already exists; stored as {version}")
            elif existing and version_key(version) < max(version_key(v) for v in taken):
                report.warnings.append("declared version is older than the latest stored version")
            ver = StrategyVersion(strategy_id=st.id, version=version, file_hash=report.sha256, file_size=report.size, filename=filename[:128],
                                  source_code=source, uploaded_by=user_id, status=StrategyVersionStatus.VALIDATED.value,
                                  meta={**md, "class_name": report.class_name}, parameters=md.get("parameters", {}),
                                  dependencies=md.get("dependencies", []), validation_report=report.to_dict())
            s.add(ver)
            try:
                await s.flush()
            except IntegrityError:
                raise ConflictError("Version conflict, please retry")
            st_out, ver_out = st, ver
        self.materialize(st_out, ver_out)
        await self.audit.record(action="STRATEGY_UPLOAD", user_id=user_id, target=f"{st_out.name}@{ver_out.version}", new_state={"sha256": report.sha256, "version": ver_out.version})
        return report, st_out, ver_out

    # ---- filesystem cache -----------------------------------------------------------------------------
    def materialize(self, st: Strategy, ver: StrategyVersion) -> Path:
        d = self.base / "_store" / st.id / ver.version
        d.mkdir(parents=True, exist_ok=True)
        (d / "strategy.py").write_text(ver.source_code, encoding="utf-8")
        (d / "metadata.json").write_text(json.dumps({"strategy": st.name, "version": ver.version, "sha256": ver.file_hash, "metadata": ver.meta}, indent=2, default=str), encoding="utf-8")
        return d

    async def restore_active(self) -> int:
        n = 0
        for st in await self.list():
            if st.active_version_id:
                try:
                    self.materialize(st, await self.get_version(st.active_version_id))
                    n += 1
                except Exception as exc:
                    log.error("restore failed for %s: %s", st.name, type(exc).__name__)
        return n

    async def load_source(self, version_id: str) -> tuple[str, StrategyVersion]:
        """Integrity-checked source (hash must match what was validated)."""
        v = await self.get_version(version_id)
        if sha256_hex(v.source_code.encode("utf-8", errors="replace")) != v.file_hash:
            raise ValidationFailed("Stored strategy source failed its integrity check")
        return v.source_code, v

    async def revalidate(self, version_id: str, user_id: int) -> ValidationReport:
        v = await self.get_version(version_id)
        rep = self.validate(v.source_code)
        async with self.db.session() as s:
            row = await s.get(StrategyVersion, version_id)
            row.validation_report = rep.to_dict()
            if not rep.ok:
                row.status, row.is_active = StrategyVersionStatus.REJECTED.value, False
        await self.audit.record(action="STRATEGY_REVALIDATE", user_id=user_id, target=version_id, result="OK" if rep.ok else "REJECTED", error="; ".join(rep.errors)[:300] or None)
        return rep

    # ---- activation / rollback ---------------------------------------------------------------------------
    async def activate(self, version_id: str, user_id: int, *, rollback: bool = False) -> StrategyVersion:
        v = await self.get_version(version_id)
        rep = self.validate(v.source_code)
        if not rep.ok:
            raise ValidationFailed("Version fails validation: " + "; ".join(rep.errors[:3]))
        async with self.db.session() as s:
            st = await s.get(Strategy, v.strategy_id)
            prev = st.active_version_id
            for row in (await s.execute(select(StrategyVersion).where(StrategyVersion.strategy_id == st.id))).scalars():
                row.is_active = row.id == version_id
                if row.id == version_id:
                    row.status, row.activated_at = StrategyVersionStatus.ACTIVE.value, utcnow()
                elif row.status == StrategyVersionStatus.ACTIVE.value:
                    row.status = StrategyVersionStatus.INACTIVE.value
            st.active_version_id = version_id
        self.materialize(st, v)
        await self.audit.record(action="STRATEGY_ROLLBACK" if rollback else "STRATEGY_ACTIVATE", user_id=user_id, target=f"{st.name}@{v.version}",
                                previous_state={"active_version_id": prev}, new_state={"active_version_id": version_id})
        return await self.get_version(version_id)

    async def deactivate(self, strategy_id: str, user_id: int) -> None:
        async with self.db.session() as s:
            st = await s.get(Strategy, strategy_id)
            prev, st.active_version_id = st.active_version_id, None
            for row in (await s.execute(select(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id, StrategyVersion.is_active.is_(True)))).scalars():
                row.is_active, row.status = False, StrategyVersionStatus.INACTIVE.value
        await self.audit.record(action="STRATEGY_DEACTIVATE", user_id=user_id, target=strategy_id, previous_state={"active_version_id": prev})

    async def rollback(self, strategy_id: str, to_version_id: str, user_id: int) -> StrategyVersion:
        v = await self.get_version(to_version_id)
        if v.strategy_id != strategy_id:
            raise ValidationFailed("Version belongs to a different strategy")
        return await self.activate(to_version_id, user_id, rollback=True)

    # ---- parameters -----------------------------------------------------------------------------------------
    async def set_param(self, version_id: str, key: str, raw: str, user_id: int) -> dict[str, Any]:
        v = await self.get_version(version_id)
        if key not in v.parameters:
            raise ValidationFailed(f"Unknown parameter '{key}'")
        cur = v.parameters[key]
        try:
            if isinstance(cur, bool):
                if raw.strip().lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    raise ValueError
                val: Any = raw.strip().lower() in ("true", "1", "yes", "on")
            elif isinstance(cur, int):
                val = int(raw)
            elif isinstance(cur, float):
                val = float(raw)
            else:
                val = raw.strip()[:200]
        except ValueError as exc:
            raise ValidationFailed(f"'{key}' expects {type(cur).__name__}") from exc
        async with self.db.session() as s:
            row = await s.get(StrategyVersion, version_id)
            params = dict(row.parameters)
            params[key] = val
            row.parameters = params
        await self.audit.record(action="STRATEGY_PARAM_SET", user_id=user_id, target=version_id, previous_state={key: cur}, new_state={key: val})
        return params

    # ---- stats / delete -----------------------------------------------------------------------------------------
    async def stats(self, strategy_id: str) -> dict[str, Any]:
        async with self.db.session() as s:
            rows = (await s.execute(select(Trade).where(Trade.strategy_id == strategy_id))).scalars().all()
            bots = (await s.execute(select(func.count()).select_from(Bot).where(Bot.strategy_id == strategy_id, Bot.deleted_at.is_(None)))).scalar_one()
        n = len(rows)
        wins = sum(1 for t in rows if t.net_pnl > 0)
        by_ver: dict[str, dict[str, float]] = {}
        for t in rows:
            d = by_ver.setdefault(t.strategy_version_id or "?", {"trades": 0, "net": 0.0})
            d["trades"] += 1
            d["net"] += t.net_pnl
        return {"trades": n, "wins": wins, "win_rate": (wins / n * 100) if n else 0.0, "net_pnl": sum(t.net_pnl for t in rows), "bots": bots, "by_version": by_ver}

    async def delete(self, strategy_id: str, user_id: int) -> None:
        async with self.db.session() as s:
            active = (await s.execute(select(func.count()).select_from(Bot).where(
                Bot.strategy_id == strategy_id, Bot.deleted_at.is_(None), Bot.status.in_(ACTIVE_BOT_STATES)))).scalar_one()
        if active:
            raise ConflictError(f"{active} bot(s) are using this strategy — stop/delete them first")
        async with self.db.session() as s:
            st = await s.get(Strategy, strategy_id)
            st.deleted_at, st.active_version_id = utcnow(), None
        await self.audit.record(action="STRATEGY_DELETE", user_id=user_id, target=strategy_id)
