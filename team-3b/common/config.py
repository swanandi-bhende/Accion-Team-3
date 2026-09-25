import os
from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # TEE
    tee_mode: str = Field(..., env="TEE_MODE")
    tee_sealed_key_path: str = Field(..., env="TEE_SEALED_KEY_PATH")
    tee_inference_timeout_s: float = Field(30.0, env="TEE_INFERENCE_TIMEOUT_S")

    # Storage
    redis_url: str = Field(..., env="REDIS_URL")
    model_storage_backend: str = Field(..., env="MODEL_STORAGE_BACKEND")
    model_storage_url: str = Field(..., env="MODEL_STORAGE_URL")
    model_max_size_mb: int = Field(500, env="MODEL_MAX_SIZE_MB")
    zerog_rpc_endpoint: str = Field("https://rpc-testnet.0g.ai", env="ZEROG_RPC_ENDPOINT")
    zerog_private_key: str = Field("", env="ZEROG_PRIVATE_KEY")

    # Blockchain
    web3_ws_url: str = Field(..., env="WEB3_WS_URL")
    web3_rpc_url: str = Field(..., env="WEB3_RPC_URL")
    task_manager_address: str = Field(..., env="TASK_MANAGER_ADDRESS")
    escrow_address: str = Field(..., env="ESCROW_ADDRESS")

    # Proof Submitter
    proof_batch_window_s: float = Field(10.0, env="PROOF_BATCH_WINDOW_S")
    proof_submitter_private_key: str = Field(..., env="PROOF_SUBMITTER_PRIVATE_KEY")
    proof_submitter_contract_address: str = Field(..., env="PROOF_SUBMITTER_CONTRACT_ADDRESS")
    
    # zkML
    zkml_proving_key_store: str = Field(..., env="ZKML_PROVING_KEY_STORE")
    zkml_supported_architectures: str = Field(..., env="ZKML_SUPPORTED_ARCHITECTURES")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        
from functools import lru_cache

@lru_cache()
def get_settings():
    try:
        return Settings()
    except ValueError as e:
        print(f"CRITICAL: Failed to load environment configuration: {e}")
        print("Ensure .env is fully populated from .env.example.")
        raise

