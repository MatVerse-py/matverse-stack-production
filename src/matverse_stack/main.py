from __future__ import annotations

import json
from contextlib import asynccontextmanager

import gradio as gr
from fastapi import FastAPI

from .api import router as api_router, service
from .config import APP_NAME, APP_VERSION, HOST, PORT


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("MatVerse STACK API starting up...")
    yield
    print("MatVerse STACK API shutting down...")


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.include_router(api_router, prefix="/api")


def process_query_ui(query: str, metadata_str: str) -> str:
    try:
        metadata = json.loads(metadata_str) if metadata_str else {}
        result = service.process_query(query, add_to_memory=False, metadata=metadata)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


def search_memory_ui(query: str, top_k: int) -> str:
    try:
        results = service.search_memory(query, top_k)
        return json.dumps([mnb.model_dump() for mnb in results], indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


def get_mnb_ui(mnb_id: str) -> str:
    try:
        mnb = service.get_mnb(mnb_id)
        if mnb:
            return json.dumps(mnb.model_dump(), indent=2, ensure_ascii=False)
        return "MNB not found"
    except Exception as exc:
        return f"Error: {exc}"


def get_ledger_ui() -> str:
    try:
        entries = service.get_ledger()
        return json.dumps([entry.model_dump() for entry in entries], indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


def get_context_ui() -> str:
    try:
        context = service.memory.get_context_window()
        return json.dumps([mnb.model_dump() for mnb in context], indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


def get_mediation_ui() -> str:
    try:
        return json.dumps(service.get_mediation_status(), indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


def close_autogenesis_ui(metadata_str: str) -> str:
    try:
        metadata = json.loads(metadata_str) if metadata_str else {}
        result = service.close_autogenesis(metadata)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


with gr.Blocks() as demo:
    gr.Markdown("# MatVerse SGSI Dashboard")
    gr.Markdown(
        "Persistent MNB state is write-governed. Direct memory writes are disabled; "
        "authorized persistence uses the typed mutation/effect API."
    )

    with gr.Tab("Process Query"):
        query_input = gr.Textbox(label="Query Input", placeholder="Enter your query here...")
        metadata_input = gr.Textbox(
            label="Metadata (JSON)",
            placeholder="{\"source\": \"gradio_ui\"}",
        )
        process_button = gr.Button("Process")
        process_output = gr.JSON(label="Process Result")
        process_button.click(
            process_query_ui,
            inputs=[query_input, metadata_input],
            outputs=process_output,
        )

    with gr.Tab("Search Memory"):
        search_query_input = gr.Textbox(label="Search Query", placeholder="Search for...")
        search_topk_input = gr.Slider(
            minimum=1,
            maximum=10,
            value=5,
            step=1,
            label="Top K Results",
        )
        search_button = gr.Button("Search")
        search_output = gr.JSON(label="Search Results")
        search_button.click(
            search_memory_ui,
            inputs=[search_query_input, search_topk_input],
            outputs=search_output,
        )

    with gr.Tab("Get MNB by ID"):
        get_mnb_id_input = gr.Textbox(label="MNB ID", placeholder="Enter MNB ID...")
        get_mnb_button = gr.Button("Get MNB")
        get_mnb_output = gr.JSON(label="MNB Details")
        get_mnb_button.click(get_mnb_ui, inputs=get_mnb_id_input, outputs=get_mnb_output)

    with gr.Tab("View Ledger"):
        view_ledger_button = gr.Button("View Ledger")
        ledger_output = gr.JSON(label="Ledger Entries")
        view_ledger_button.click(get_ledger_ui, outputs=ledger_output)

    with gr.Tab("Context Buffer"):
        view_context_button = gr.Button("View Context Buffer")
        context_output = gr.JSON(label="Active Context (STM/Buffer)")
        view_context_button.click(get_context_ui, outputs=context_output)

    with gr.Tab("Mediation Status"):
        mediation_button = gr.Button("View Constitutional Write Policy")
        mediation_output = gr.JSON(label="Global Mediation")
        mediation_button.click(get_mediation_ui, outputs=mediation_output)

    with gr.Tab("Autogenesis"):
        gr.Markdown("### Fechar Ciclo de Autogênese")
        autogenesis_metadata = gr.Textbox(label="Metadata (JSON)", placeholder="{}")
        close_button = gr.Button("Fechar Ciclo (Genesis + Anchor + Receipts)")
        autogenesis_output = gr.JSON(label="Resultado do Fechamento")
        close_button.click(
            close_autogenesis_ui,
            inputs=autogenesis_metadata,
            outputs=autogenesis_output,
        )


app = gr.mount_gradio_app(app, demo, path="/")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
