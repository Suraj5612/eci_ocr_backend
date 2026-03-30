from pydantic import BaseModel

class VoterUpdateRequest(BaseModel):
    name: str | None = None
    epic: str | None = None
    mobile: str | None = None
    address: str | None = None
    serial_number: int | None = None
    part_number_and_name: str | None = None
    assembly_constituency_id: int
    district: str | None = None
    state: str | None = None