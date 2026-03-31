import csv
import os
from datetime import datetime

def generate_csv(voters):
    os.makedirs("exports", exist_ok=True)

    filename = f"exports/voters_{datetime.utcnow().timestamp()}.csv"

    with open(filename, mode="w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)

        writer.writerow([
            "ID", "Name", "EPIC", "Mobile",
            "Address", "Serial Number", "Part",
            "Constituency", "District", "State"
        ])

        for v in voters:
            writer.writerow([
                str(v.id),
                v.name,
                v.epic,
                v.mobile,
                v.address,
                v.serial_number,
                v.part_number_and_name,
                v.assembly_constituency_name,
                v.district,
                v.state
            ])
    return filename