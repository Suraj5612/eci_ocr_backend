# app/schemas/voter.py

from pydantic import BaseModel


class VoterCreate(BaseModel):
    name: str
    epic: str | None = None
    mobile: str | None = None
    address: str | None = None
    serial_number: int | None = None
    part_number_and_name: str | None = None

    assembly_constituency_name: str

    district: str | None = None
    state: str | None = None