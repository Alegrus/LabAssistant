"""Environment-driven application settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    secret_key: str = "change-me"
    base_url: str = "http://localhost:8000"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/labassistant"

    # --- LLM provider ---
    # "openrouter" = hosted API; "local" = any OpenAI-compatible local server
    # (Ollama, LM Studio, MLX). The pipeline code only reads the resolved
    # `llm_*` properties below, so switching providers is a config change.
    llm_provider: str = "openrouter"

    # OpenRouter (hosted)
    openrouter_api_key: str = ""
    openrouter_chat_model: str = "anthropic/claude-sonnet-4.5"
    openrouter_vision_model: str = "anthropic/claude-sonnet-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Local (OpenAI-compatible server, e.g. Ollama on :11434). The key is a
    # placeholder — local servers ignore it but the OpenAI client requires one.
    local_api_key: str = "local"
    local_chat_model: str = "qwen2.5-vl:7b"
    local_vision_model: str = "qwen2.5-vl:7b"
    local_base_url: str = "http://localhost:11434/v1"

    # Non-streaming LLM request timeout (seconds). Local models can cold-load (a 12B is
    # ~15s) and prefill a large RAG context, so this is generous by default.
    llm_request_timeout: float = 180.0

    # Many local models emit hidden "thinking" tokens before the answer — on a grounded
    # RAG turn that was ~90% of generation time (1582 tokens emitted for a 140-token
    # answer) for no quality gain, since the manual text is already in the prompt.
    # "none" disables it; set "low"/"medium"/"high" (or "" to omit) to re-enable.
    # Only sent when llm_provider == "local"; hosted models are left untouched.
    llm_reasoning_effort: str = "none"

    @property
    def llm_base_url(self) -> str:
        return self.local_base_url if self.llm_provider == "local" else self.openrouter_base_url

    @property
    def llm_api_key(self) -> str:
        return self.local_api_key if self.llm_provider == "local" else self.openrouter_api_key

    @property
    def llm_chat_model(self) -> str:
        return self.local_chat_model if self.llm_provider == "local" else self.openrouter_chat_model

    @property
    def llm_vision_model(self) -> str:
        return self.local_vision_model if self.llm_provider == "local" else self.openrouter_vision_model

    # --- Embeddings (local, sentence-transformers) ---
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_dim: int = 768  # must match the model (bge-base=768, bge-small=384)
    embedding_batch_size: int = 32
    # bge-*-en-v1.5 retrieval works best with this instruction prepended to QUERIES
    # only (not passages). Set empty for models that don't want it.
    embedding_query_instruction: str = (
        "Represent this sentence for searching relevant passages: "
    )

    # --- Reranker (cross-encoder) ---
    reranker_model: str = "BAAI/bge-reranker-base"

    # --- Chunking (token-aware) ---
    chunk_tokens: int = 380       # target chunk size in tokens (bge max seq = 512)
    chunk_overlap_tokens: int = 64

    # --- Retrieval ---
    retrieval_dense_k: int = 40   # candidates pulled from the vector index
    retrieval_sparse_k: int = 40  # candidates pulled from full-text search
    rrf_k: int = 60               # Reciprocal Rank Fusion constant
    rerank_candidates: int = 30   # fused pool size handed to the reranker
    retrieval_top_k: int = 6      # final chunks kept after reranking
    rerank_score_threshold: float = 0.0  # drop chunks the reranker scores below this

    # --- Context expansion ---
    # After reranking, stitch each kept chunk together with its neighbors (same doc,
    # adjacent ordinals) so multi-step procedures aren't cut off mid-list. Bias to
    # "after" since procedures continue forward. Set both to 0 to disable.
    context_expand_before: int = 1
    context_expand_after: int = 2
    context_max_passages: int = 6  # cap on stitched passages handed to the LLM

    # Preload the embedding + reranker models at startup (in a background thread) so
    # the first user query doesn't pay the model-load cost. Disable for fast boots or
    # environments where the models aren't needed.
    warm_models_on_startup: bool = True

    # Also preload the remote chat/vision models on a local Ollama at startup, so the
    # first question (and the first photo) don't pay a cold load of 15-20s.
    warm_llm_on_startup: bool = True
    # How long Ollama keeps a model resident after use. Ollama's own default is 5m, so
    # an idle lab would cold-load again between questions. "-1" pins it forever — only
    # sensible on a dedicated box, since a large context window can cost tens of GB.
    llm_keep_alive: str = "2h"

    # --- RAG ---
    rag_max_context_tokens: int = 4000
    rag_not_found_message: str = "I couldn't find that in the available manuals."

    # Uploads
    max_upload_mb: int = 100  # hard ceiling per file (streamed to disk, then ingested synchronously)
    max_image_mb: int = 25    # hard ceiling for a chat photo (Phase 4 vision)
    # Long-edge cap for photos sent to the vision model. Phone cameras produce ~12 MP
    # images; downscaling cuts upload + prefill time with no loss of on-screen legibility.
    vision_max_image_px: int = 1536
    max_batch_mb: int = 500   # hard ceiling for one multi-file upload (disk + request-time guard)
    upload_warn_mb: int = 15  # soft "large file, may be slow" warning + per-file confirm threshold
    upload_retention_days: int = 0  # 0 = keep until an admin deletes (N3)

    # Seed passwords (rotate via admin UI after first run)
    initial_user_password: str = "changeme-user"
    initial_admin_password: str = "changeme-admin"

    # --- Sessions / auth ---
    session_cookie_name: str = "session"
    session_max_age_seconds: int = 60 * 60 * 12  # 12h
    device_cookie_name: str = "device_id"
    device_id_max_age_seconds: int = 60 * 60 * 24 * 365  # ~1 year
    login_rate_limit: str = "10/minute"


settings = Settings()
