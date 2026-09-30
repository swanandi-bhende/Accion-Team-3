from pydantic import BaseModel
import base64
class P(BaseModel):
    b: bytes

raw_bytes = b"\xff\x00\xaa"
payload = {"b": raw_bytes}
p = P.model_validate(payload)
print(p.b == raw_bytes)
