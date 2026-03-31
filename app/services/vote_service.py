from app.models.voter import Voter
from app.utils.exceptions import AppException


def get_base_query(db, current_user):
    query = db.query(Voter)

    role = current_user.role

    if role == "booth":
        query = query.filter(Voter.user_id == current_user.id)

    elif role == "constituency":
        query = query.filter(
            Voter.assembly_constituency_id == current_user.constituency_id
        )

    elif role == "district":
        query = query.filter(
            Voter.district_id == current_user.district_id
        )

    elif role == "mandal":
        query = query.filter(
            Voter.mandal_id == current_user.mandal_id
        )

    elif role == "superadmin":
        pass

    else:
        raise AppException(
            status_code=403,
            code="INVALID_ROLE",
            message="Invalid role"
        )

    return query