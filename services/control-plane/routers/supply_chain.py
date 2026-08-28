"""
Supply-Chain Management Router

CRUD for the package allowlist that governs what agents can install.
Replaces the static config/supply-chain.yaml with DB-backed management.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func as sa_func, and_
from typing import List, Optional
import logging
import yaml
from pathlib import Path

from database import get_db
from models import SupplyChainPackage, SupplyChainAlias, SupplyChainImageType
from schemas import (
    SupplyChainPackageCreate,
    SupplyChainPackageResponse,
    SupplyChainBulkAdd,
    SupplyChainAliasCreate,
    SupplyChainAliasResponse,
    SupplyChainImageTypeCreate,
    SupplyChainImageTypeResponse,
    SupplyChainImageTypeSummary,
    SupplyChainFullConfig,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/supply-chain", tags=["supply-chain"])


# =============================================================================
# Full config endpoint (mirrors the old YAML structure for image-builder)
# =============================================================================

@router.get("/config", response_model=SupplyChainFullConfig)
async def get_supply_chain_config(db: AsyncSession = Depends(get_db)):
    """Return the full supply-chain configuration (for image-builder & audit UI)."""

    # Fetch all image types
    result = await db.execute(select(SupplyChainImageType))
    image_types_rows = result.scalars().all()

    # Fetch all packages
    result = await db.execute(select(SupplyChainPackage))
    packages = result.scalars().all()

    # Fetch all aliases
    result = await db.execute(select(SupplyChainAlias))
    aliases_rows = result.scalars().all()

    # Build summaries
    summaries = []
    raw = {}
    for it in image_types_rows:
        type_pkgs = [p for p in packages if p.image_type == it.image_type]
        pip_pkgs = [p.package_name for p in type_pkgs if p.manager == "pip"]
        apt_pkgs = [p.package_name for p in type_pkgs if p.manager == "apt"]
        apk_pkgs = [p.package_name for p in type_pkgs if p.manager == "apk"]
        npm_pkgs = [p.package_name for p in type_pkgs if p.manager == "npm"]
        exceptions = len([p for p in type_pkgs if p.is_exception == "true"])

        summaries.append(SupplyChainImageTypeSummary(
            image_type=it.image_type,
            notes=it.notes,
            pip=len(pip_pkgs),
            apt=len(apt_pkgs),
            apk=len(apk_pkgs),
            npm=len(npm_pkgs),
            exceptions=exceptions,
        ))

        raw[it.image_type] = {
            "notes": it.notes or "",
            "pip": pip_pkgs,
            "apt": apt_pkgs,
            "apk": apk_pkgs,
            "npm": npm_pkgs,
        }

    # Build aliases dict
    aliases = {}
    for a in aliases_rows:
        if a.direction not in aliases:
            aliases[a.direction] = {}
        aliases[a.direction][a.from_name] = a.to_name

    return SupplyChainFullConfig(
        image_types=summaries,
        aliases=aliases,
        raw=raw,
    )


# =============================================================================
# Image Types CRUD
# =============================================================================

@router.get("/image-types", response_model=List[SupplyChainImageTypeResponse])
async def list_image_types(db: AsyncSession = Depends(get_db)):
    """List all image types."""
    result = await db.execute(
        select(SupplyChainImageType).order_by(SupplyChainImageType.image_type)
    )
    return result.scalars().all()


@router.post("/image-types", response_model=SupplyChainImageTypeResponse,
             status_code=status.HTTP_201_CREATED)
async def create_image_type(
    data: SupplyChainImageTypeCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new image type."""
    existing = await db.execute(
        select(SupplyChainImageType).where(
            SupplyChainImageType.image_type == data.image_type
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Image type '{data.image_type}' already exists")

    row = SupplyChainImageType(image_type=data.image_type, notes=data.notes)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/image-types/{image_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image_type(image_type: str, db: AsyncSession = Depends(get_db)):
    """Delete an image type and all its packages."""
    await db.execute(
        delete(SupplyChainPackage).where(SupplyChainPackage.image_type == image_type)
    )
    result = await db.execute(
        delete(SupplyChainImageType).where(SupplyChainImageType.image_type == image_type)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Image type not found")


# =============================================================================
# Packages CRUD
# =============================================================================

@router.get("/packages", response_model=List[SupplyChainPackageResponse])
async def list_packages(
    image_type: Optional[str] = None,
    manager: Optional[str] = None,
    exception_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """List packages, optionally filtered by image_type, manager, or exceptions."""
    q = select(SupplyChainPackage).order_by(
        SupplyChainPackage.image_type,
        SupplyChainPackage.manager,
        SupplyChainPackage.package_name,
    )
    if image_type:
        q = q.where(SupplyChainPackage.image_type == image_type)
    if manager:
        q = q.where(SupplyChainPackage.manager == manager)
    if exception_only:
        q = q.where(SupplyChainPackage.is_exception == "true")

    result = await db.execute(q)
    return result.scalars().all()


@router.post("/packages", response_model=SupplyChainPackageResponse,
             status_code=status.HTTP_201_CREATED)
async def add_package(
    data: SupplyChainPackageCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a single package to the allowlist (or as an exception)."""
    # Check for duplicate
    existing = await db.execute(
        select(SupplyChainPackage).where(
            and_(
                SupplyChainPackage.image_type == data.image_type,
                SupplyChainPackage.manager == data.manager,
                SupplyChainPackage.package_name == data.package_name,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"'{data.package_name}' already exists in {data.image_type}/{data.manager}",
        )

    row = SupplyChainPackage(
        image_type=data.image_type,
        manager=data.manager,
        package_name=data.package_name,
        notes=data.notes,
        is_exception="true" if data.is_exception else "false",
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    logger.info(f"✅ Supply-chain: added {data.package_name} to {data.image_type}/{data.manager}"
                f"{' (exception)' if data.is_exception else ''}")
    return row


@router.post("/packages/bulk", response_model=dict)
async def add_packages_bulk(
    data: SupplyChainBulkAdd,
    db: AsyncSession = Depends(get_db),
):
    """Add multiple packages at once."""
    added = 0
    skipped = 0
    for pkg_name in data.packages:
        existing = await db.execute(
            select(SupplyChainPackage).where(
                and_(
                    SupplyChainPackage.image_type == data.image_type,
                    SupplyChainPackage.manager == data.manager,
                    SupplyChainPackage.package_name == pkg_name,
                )
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        db.add(SupplyChainPackage(
            image_type=data.image_type,
            manager=data.manager,
            package_name=pkg_name,
            notes=data.notes,
            is_exception="true" if data.is_exception else "false",
        ))
        added += 1

    await db.flush()
    return {"added": added, "skipped": skipped, "total": len(data.packages)}


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(package_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a package from the allowlist."""
    result = await db.execute(
        delete(SupplyChainPackage).where(SupplyChainPackage.id == package_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Package not found")


# =============================================================================
# Aliases CRUD
# =============================================================================

@router.get("/aliases", response_model=List[SupplyChainAliasResponse])
async def list_aliases(
    direction: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all aliases, optionally filtered by direction."""
    q = select(SupplyChainAlias).order_by(SupplyChainAlias.direction, SupplyChainAlias.from_name)
    if direction:
        q = q.where(SupplyChainAlias.direction == direction)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/aliases", response_model=SupplyChainAliasResponse,
             status_code=status.HTTP_201_CREATED)
async def add_alias(
    data: SupplyChainAliasCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a cross-distro alias mapping."""
    if data.direction not in ("apt_to_apk", "apk_to_apt"):
        raise HTTPException(status_code=400, detail="direction must be 'apt_to_apk' or 'apk_to_apt'")

    existing = await db.execute(
        select(SupplyChainAlias).where(
            and_(
                SupplyChainAlias.direction == data.direction,
                SupplyChainAlias.from_name == data.from_name,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Alias '{data.from_name}' already exists for {data.direction}")

    row = SupplyChainAlias(
        direction=data.direction,
        from_name=data.from_name,
        to_name=data.to_name,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(alias_id: int, db: AsyncSession = Depends(get_db)):
    """Remove an alias mapping."""
    result = await db.execute(
        delete(SupplyChainAlias).where(SupplyChainAlias.id == alias_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alias not found")


# =============================================================================
# Seed from YAML — import the static config into the database
# =============================================================================

@router.post("/seed")
async def seed_from_yaml(db: AsyncSession = Depends(get_db)):
    """Import packages from the static supply-chain.yaml into the database.

    This is idempotent — existing entries are skipped.
    """
    yaml_path = Path("/config/supply-chain.yaml")
    # Fallback for local development
    if not yaml_path.exists():
        yaml_path = Path(__file__).parent.parent.parent.parent / "config" / "supply-chain.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail="supply-chain.yaml not found")

    raw = yaml.safe_load(yaml_path.read_text())
    aliases_data = raw.pop("aliases", {})

    added_pkgs = 0
    added_aliases = 0
    added_types = 0

    # Import image types and packages
    for image_type, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue

        # Ensure image type exists
        existing_type = await db.execute(
            select(SupplyChainImageType).where(
                SupplyChainImageType.image_type == image_type
            )
        )
        if not existing_type.scalar_one_or_none():
            db.add(SupplyChainImageType(
                image_type=image_type,
                notes=cfg.get("notes", ""),
            ))
            added_types += 1

        for manager in ("pip", "apt", "apk", "npm"):
            for pkg_name in cfg.get(manager, []):
                existing = await db.execute(
                    select(SupplyChainPackage).where(
                        and_(
                            SupplyChainPackage.image_type == image_type,
                            SupplyChainPackage.manager == manager,
                            SupplyChainPackage.package_name == pkg_name,
                        )
                    )
                )
                if not existing.scalar_one_or_none():
                    db.add(SupplyChainPackage(
                        image_type=image_type,
                        manager=manager,
                        package_name=pkg_name,
                    ))
                    added_pkgs += 1

    # Import aliases
    for direction, mappings in aliases_data.items():
        for from_name, to_name in mappings.items():
            existing = await db.execute(
                select(SupplyChainAlias).where(
                    and_(
                        SupplyChainAlias.direction == direction,
                        SupplyChainAlias.from_name == from_name,
                    )
                )
            )
            if not existing.scalar_one_or_none():
                db.add(SupplyChainAlias(
                    direction=direction,
                    from_name=from_name,
                    to_name=to_name,
                ))
                added_aliases += 1

    await db.flush()
    logger.info(f"✅ Supply-chain seed: {added_types} image types, "
                f"{added_pkgs} packages, {added_aliases} aliases")
    return {
        "status": "seeded",
        "added_image_types": added_types,
        "added_packages": added_pkgs,
        "added_aliases": added_aliases,
    }
