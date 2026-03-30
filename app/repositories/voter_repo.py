from sqlalchemy.orm import Session
from app.models.voter import Voter

def create_voter(db: Session, data: dict):
    existing = db.query(Voter).filter(
        Voter.epic == data.get("epic"),
        Voter.assembly_constituency_id == data.get("assembly_constituency_id")
    ).first()

    if existing:
        return None

    voter = Voter(**data)

    db.add(voter)
    db.commit()
    db.refresh(voter)

    return voter

def update_voter(db: Session, voter_id: str, data: dict):
    if "assembly_constituency_id" not in data:
        raise Exception("assembly_constituency_id is required")

    voter = (
        db.query(Voter)
        .filter(
            Voter.id == voter_id,
            Voter.assembly_constituency_id == data["assembly_constituency_id"]
        )
        .first()
    )

    if not voter:
        return None

    # 🔥 prevent partition change
    data.pop("assembly_constituency_id", None)

    for key, value in data.items():
        if value is not None:
            setattr(voter, key, value)

    db.commit()
    db.refresh(voter)

    return voter

def delete_voter(db: Session, voter_id: str, assembly_constituency_id: int):
    voter = (
        db.query(Voter)
        .filter(
            Voter.id == voter_id,
            Voter.assembly_constituency_id == assembly_constituency_id
        )
        .first()
    )

    if not voter:
        return False

    db.delete(voter)
    db.commit()

    return True

def get_total_voters(db: Session):
    return db.query(Voter).count()