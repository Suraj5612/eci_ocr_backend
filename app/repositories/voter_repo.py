from uuid import UUID

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

def update_voter(db: Session, voter_id: str, ac_id: int, data: dict):
    voter = (
        db.query(Voter)
        .filter(
            Voter.id == voter_id,
            Voter.assembly_constituency_id == ac_id  # 🔥 partition targeting
        )
        .first()
    )

    if not voter:
        return None

    for key, value in data.items():
        if value is not None:
            setattr(voter, key, value)

    db.commit()
    db.refresh(voter)

    return voter

def delete_voter(db: Session, voter_id: UUID, ac_id: int):
    voter = (
        db.query(Voter)
        .filter(
            Voter.id == voter_id,
            Voter.assembly_constituency_id == ac_id
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