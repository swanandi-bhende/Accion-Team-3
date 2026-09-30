from pydantic import BaseModel
class P(BaseModel):
    b: bytes
p = P(b=b'hello')
j = p.model_dump_json()
print("json:", j)

# If we load from dict:
import json
d = json.loads(j)
p2 = P.model_validate(d)
print("from dict:", p2.b)

# If we load from json directly:
p3 = P.model_validate_json(j)
print("from json:", p3.b)
