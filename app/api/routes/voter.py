# app/api/routes/voter.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.constituency import Constituency
from app.models.voter import Voter
from app.schemas.voter import VoterCreate
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.voter_delete_request import VoterDeleteRequest
from app.utils.success_response import success_response
from app.utils.exceptions import AppException
from app.schemas.voter_update_request import VoterUpdateRequest
from app.repositories.voter_repo import create_voter, delete_voter, get_total_voters, update_voter

router = APIRouter()


@router.post("/save")
def create_voter_api(
    payload: VoterCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    constituency = (
        db.query(Constituency)
        .filter(
            Constituency.constituency_hindi == payload.assembly_constituency_name
        )
        .first()
    )

    if not constituency:
        raise AppException(
            status_code=400,
            code="INVALID_CONSTITUENCY",
            message="Invalid assembly constituency"
        )

    data = payload.model_dump()

    data.pop("assembly_constituency_name", None)

    data["assembly_constituency_id"] = constituency.id
    data["assembly_constituency_name"] = constituency.constituency_hindi
    data["user_id"] = current_user.id

    voter = create_voter(db, data)

    return success_response(
        data={
            "id": voter.id,
            "message": "Voter saved successfully"
        }
    )
    
@router.put("/{voter_id}")
def update_voter_api(
    voter_id: str,
    payload: VoterUpdateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    voter = update_voter(db, voter_id, payload.model_dump())

    if not voter:
        raise AppException(
            status_code=404,
            code="VOTER_NOT_FOUND",
            message="Voter not found"
        )

    return success_response(
        data={
            "id": voter.id,
            "message": "Voter updated successfully"
        }
    )

@router.delete("/{voter_id}")
def delete_voter_api(
    voter_id: str,
    payload: VoterDeleteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    deleted = delete_voter(
        db,
        voter_id,
        payload.assembly_constituency_id
    )

    if not deleted:
        raise AppException(
            status_code=404,
            code="VOTER_NOT_FOUND",
            message="Voter not found"
        )

    return success_response(
        data={
            "id": voter_id,
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