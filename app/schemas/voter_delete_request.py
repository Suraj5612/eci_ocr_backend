from pydantic import BaseModel


class VoterDeleteRequest(BaseModel):
    assembly_constituency_id: int