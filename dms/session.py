from dataclasses import dataclass, asdict
from typing import Optional
import json


@dataclass
class SessionData:
    rig: str
    brand: str
    model: str
    model_number: str = ""
    asset_tag: str = ""
    firmware: str = ""
    eq_applied: bool = False
    anc_mode: bool = False
    transparency_mode: bool = False
    form_factor: str = "over-ear"
    in_ear_fitment: str = ""
    open_back: bool = True
    pads_notes: str = ""
    connection: str = "wired analog"
    channel_side: str = ""

    def display_name(self) -> str:
        if self.asset_tag:
            return f"{self.asset_tag} — {self.brand} {self.model}"
        return f"{self.brand} {self.model}"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_rew_header(self) -> str:
        lines = [
            "* DMS Fastgraph measurement",
            f"* Rig: {self.rig}",
            f"* Brand: {self.brand}",
            f"* Model: {self.model}",
        ]
        if self.model_number:
            lines.append(f"* Model Number: {self.model_number}")
        if self.asset_tag:
            lines.append(f"* Asset Tag: {self.asset_tag}")
        if self.firmware:
            lines.append(f"* Firmware: {self.firmware}")
        lines.append(f"* EQ Applied: {'Yes' if self.eq_applied else 'No'}")
        if self.anc_mode:
            anc_line = "ANC"
        elif self.transparency_mode:
            anc_line = "Transparency"
        else:
            anc_line = "Off"
        lines.append(f"* ANC/Transparency: {anc_line}")
        lines.append(f"* Form Factor: {self.form_factor}")
        if self.form_factor == "in-ear" and self.in_ear_fitment:
            lines.append(f"* In-ear Fitment: {self.in_ear_fitment}")
        lines.append(
            f"* Acoustic Type: {'Open Back' if self.open_back else 'Closed Back'}"
        )
        if self.pads_notes:
            lines.append(f"* Pads/Tips Notes: {self.pads_notes}")
        lines.append(f"* Connection: {self.connection}")
        if self.channel_side.strip():
            lines.append(f"* Channel Side: {self.channel_side.strip().upper()}")
        return "\n".join(lines)
