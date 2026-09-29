"""Chat and conversation endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services import chat_service, dataset_service
from app.utils.logger import get_logger

chat_bp = Blueprint("chat", __name__, url_prefix="/api")
log = get_logger("routes.chat")


@chat_bp.post("/chat")
def post_chat():
    payload = request.get_json(silent=True) or {}
    response = chat_service.ask(
        question=payload.get("question", ""),
        dataset_id=payload.get("dataset_id") or None,
        conversation_id=payload.get("conversation_id") or None,
    )
    return jsonify(response), 200


@chat_bp.get("/chat/suggestions")
def get_suggestions():
    dataset_id = request.args.get("dataset_id")
    return jsonify({"suggestions": dataset_service.starter_questions(dataset_id)})


@chat_bp.get("/conversations/<conversation_id>")
def get_conversation(conversation_id: str):
    return jsonify({"messages": chat_service.conversation_messages(conversation_id)})


@chat_bp.delete("/conversations/<conversation_id>")
def reset_conversation(conversation_id: str):
    chat_service.reset_conversation(conversation_id)
    return jsonify({"status": "cleared", "conversation_id": conversation_id})
