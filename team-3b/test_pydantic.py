from pydantic import BaseModel
class M(BaseModel):
    b: bytes

m1 = M.model_validate_json('{"b": "e30="}')
print(m1.b)
