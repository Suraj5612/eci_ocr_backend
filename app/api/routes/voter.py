# app/api/routes/voter.py

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.constituency import Constituency
from app.models.districts import District
from app.models.voter import Voter
from app.schemas.voter import VoterCreate
from app.api.deps import get_current_user
from app.models.user import User
from app.services.csv_service import generate_csv
from app.services.vote_service import get_base_query
from app.utils.success_response import success_response
from app.utils.exceptions import AppException
from app.schemas.voter_update_request import VoterUpdateRequest
from app.repositories.voter_repo import create_voter, delete_voter, get_total_voters, update_voter

router = APIRouter()

@router.get("/getVoters")
def get_voters(
    epic: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    try:
        if epic:
            booth_id = current_user.booth_id

            if not booth_id:
                raise AppException(
                    status_code=403,
                    code="NO_BOOTH_ASSIGNED",
                    message="Your account has no booth assigned"
                )

            voter = (
                db.query(Voter)
                .filter(
                    Voter.epic == epic.strip(),
                    Voter.booth_id == booth_id
                )
                .first()
            )

            if not voter:
                raise AppException(
                    status_code=404,
                    code="VOTER_NOT_FOUND",
                    message="No voter found with this EPIC in your booth"
                )

            return success_response(
                data=[
                    {
                        "id": str(voter.id),
                        "name": voter.name,
                        "epic": voter.epic,
                        "mobile": voter.mobile,
                        "address": voter.address,
                        "serial_number": voter.serial_number,
                        "part_number_and_name": voter.part_number_and_name,
                        "assembly_constituency_id": voter.assembly_constituency_id,
                        "assembly_constituency_name": voter.assembly_constituency_name,
                        "district": voter.district,
                        "state": voter.state,
                        "mandal_id": voter.mandal_id,
                        "district_id": voter.district_id,
                        "booth_id": voter.booth_id,
                        "user_id": str(voter.user_id)
                    }
                ]
            )

        query = get_base_query(db, current_user)

        offset = (page - 1) * limit
        voters = query.offset(offset).limit(limit).all()

        return success_response(
            data=[
                {
                    "id": str(v.id),
                    "name": v.name,
                    "epic": v.epic,
                    "mobile": v.mobile,
                    "address": v.address,
                    "serial_number": v.serial_number,
                    "part_number_and_name": v.part_number_and_name,
                    "assembly_constituency_id": v.assembly_constituency_id,
                    "assembly_constituency_name": v.assembly_constituency_name,
                    "district": v.district,
                    "state": v.state,
                    "mandal_id": v.mandal_id,
                    "district_id": v.district_id,
                    "booth_id": v.booth_id,
                    "user_id": str(v.user_id)
                }
                for v in voters
            ]
        )

    except AppException:
        raise

    except Exception as e:
        raise AppException(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message=f"Something went wrong while fetching voters: {str(e)}"
        )

@router.post("/save")
def create_voter_api(
    payload: VoterCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    from sqlalchemy import or_, func

    try:
        if not payload.assembly_constituency_name:
            raise AppException(
                status_code=400,
                code="INVALID_INPUT",
                message="Assembly constituency name is required",
                field="assembly_constituency_name"
            )

        name = payload.assembly_constituency_name.strip().lower()

        constituency = (
            db.query(Constituency)
            .filter(
                or_(
                    func.lower(Constituency.constituency_hindi) == name,
                    func.lower(Constituency.constituency) == name
                )
            )
            .first()
        )

        if not constituency:
            raise AppException(
                status_code=400,
                code="INVALID_CONSTITUENCY",
                message="Invalid assembly constituency",
                field="assembly_constituency_name"
            )

        if payload.epic:
            existing = (
                db.query(Voter)
                .filter(
                    Voter.epic == payload.epic,
                    Voter.assembly_constituency_id == constituency.id
                )
                .first()
            )

            if existing:
                raise AppException(
                    status_code=400,
                    code="EPIC_ALREADY_EXISTS",
                    message="Voter with this EPIC already exists",
                    field="epic"
                )

        district = (
            db.query(District)
            .filter(District.district_id == constituency.district_id)
            .first()
        )

        data = payload.model_dump()

        data.pop("assembly_constituency_name", None)

        data["assembly_constituency_id"] = constituency.id
        data["assembly_constituency_name"] = constituency.constituency_hindi

        data["district_id"] = constituency.district_id
        data["mandal_id"] = district.mandala_id if district else None

        data["booth_id"] = current_user.booth_id
        data["user_id"] = current_user.id

        data["district"] = (
            district.district_name_hi or district.district_name_en
        ) if district else None

        voter = create_voter(db, data)

        return success_response(
            data={
                "id": voter.id,
                "message": "Voter saved successfully"
            }
        )

    except AppException:
        raise

    except Exception as e:
        raise AppException(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message=str(e)
        )
    
@router.put("/{voter_id}")
def update_voter_api(
    voter_id: str,
    ac_id: int,
    payload: VoterUpdateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    try:
        data = payload.model_dump(exclude_unset=True)
        data.pop("assembly_constituency_name", None)

        voter = update_voter(db, voter_id, ac_id, data)

        if not voter:
            raise AppException(
                status_code=404,
                code="VOTER_NOT_FOUND",
                message="Voter not found",
                field="voter_id"
            )

        return success_response(
            data={
                "id": voter.id,
                "message": "Voter updated successfully"
            }
        )

    except AppException:
        raise

    except Exception:
        raise AppException(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message="Something went wrong while updating voter"
        )

@router.delete("/{voter_id}")
def delete_voter_api(
    voter_id: str,
    ac_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # 🔥 validate UUID
    try:
        voter_id = UUID(voter_id)
    except Exception:
        raise AppException(
            status_code=400,
            code="INVALID_ID",
            message="Invalid voter ID"
        )

    deleted = delete_voter(
        db,
        voter_id,
        ac_id
    )

    if not deleted:
        raise AppException(
            status_code=404,
            code="VOTER_NOT_FOUND",
            message="Voter not found"
        )

    return success_response(
        data={
            "id": str(voter_id),
            "message": "Voter deleted successfully"
        }
    )
@router.get("/count")
def get_voter_count(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    total = get_total_voters(db)

    return success_response(
        data={
            "total_voters": total
        }
    )

from fastapi.responses import FileResponse
from typing import Optional
from fastapi import Query

@router.get("/export")
def export_voters(
    name: Optional[str] = Query(None),
    mobile: Optional[str] = Query(None),
    epic: Optional[str] = Query(None),
    assembly_constituency_id: Optional[int] = Query(None),
    district_id: Optional[int] = Query(None),

    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    query = get_base_query(db, current_user)

    # -----------------------
    # FILTERS (same as GET)
    # -----------------------
    if name:
        query = query.filter(Voter.name.ilike(f"%{name}%"))

    if mobile:
        query = query.filter(Voter.mobile.ilike(f"%{mobile}%"))

    if epic:
        query = query.filter(Voter.epic.ilike(f"%{epic}%"))

    if assembly_constituency_id:
        query = query.filter(
            Voter.assembly_constituency_id == assembly_constituency_id
        )

    if district_id:
        query = query.filter(Voter.district_id == district_id)

    voters = query.all()

    # generate csv
    file_path = generate_csv(voters)

    return FileResponse(
        path=file_path,
        filename="voters_export.csv",
        media_type="text/csv"
    )