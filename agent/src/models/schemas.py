from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ChatMessage(BaseModel):
    id: int | None = None
    chat_id: int
    role: MessageRole
    content: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ConversationContext(BaseModel):
    chat_id: int
    messages: list[ChatMessage] = Field(default_factory=list)
    system_prompt: str = (
        "Eres Rafita, un asistente virtual personal experto en productividad, "
        "finanzas y organización. Respondes en español de manera clara y concisa. "
        "Puedes ayudar con gestión de eventos, alertas, análisis financiero, "
        "y responder preguntas generales usando tu conocimiento."
    )
    max_history: int = 50

    def add_message(self, role: MessageRole, content: str) -> ChatMessage:
        msg = ChatMessage(
            chat_id=self.chat_id,
            role=role,
            content=content,
            created_at=datetime.utcnow(),
        )
        self.messages.append(msg)
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history :]
        return msg

    def to_ollama_messages(self) -> list[dict[str, str]]:
        result = [{"role": "system", "content": self.system_prompt}]
        for msg in self.messages:
            result.append({"role": msg.role.value, "content": msg.content})
        return result


class Event(BaseModel):
    id: int | None = None
    chat_id: int
    title: str
    description: str | None = None
    event_datetime: datetime
    created_at: datetime | None = None
    is_active: bool = True

    model_config = {"from_attributes": True}


class Alert(BaseModel):
    id: int | None = None
    chat_id: int
    message: str
    alert_type: str = "info"
    created_at: datetime | None = None
    expires_at: datetime | None = None
    is_read: bool = False

    model_config = {"from_attributes": True}


class FinanceCategory(str, Enum):
    income = "income"
    expense = "expense"
    transfer = "transfer"
    investment = "investment"


class FinanceRecord(BaseModel):
    id: int | None = None
    chat_id: int
    amount: float
    category: FinanceCategory
    subcategory: str | None = None
    description: str | None = None
    currency: str = "MXN"
    recorded_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class FinanceSummary(BaseModel):
    total_income: float = 0.0
    total_expenses: float = 0.0
    balance: float = 0.0
    expense_by_category: dict[str, float] = Field(default_factory=dict)
    income_by_category: dict[str, float] = Field(default_factory=dict)
    period_start: datetime | None = None
    period_end: datetime | None = None
    transaction_count: int = 0


class ExportRequest(BaseModel):
    id: int | None = None
    chat_id: int
    export_type: str
    status: str = "pending"
    file_path: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}


class BotCommand(BaseModel):
    command: str
    description: str
    admin_only: bool = False


COMMANDS_REGISTRY: list[BotCommand] = [
    BotCommand(command="start", description="Iniciar el asistente"),
    BotCommand(command="ayuda", description="Mostrar ayuda y comandos disponibles"),
    BotCommand(command="chat", description="Chatear con Rafita (IA)"),
    BotCommand(command="evento", description="Agregar un evento o recordatorio"),
    BotCommand(command="eventos", description="Listar eventos próximos"),
    BotCommand(command="alerta", description="Crear una alerta"),
    BotCommand(command="alertas", description="Listar alertas activas"),
    BotCommand(command="gasto", description="Registrar un gasto"),
    BotCommand(command="ingreso", description="Registrar un ingreso"),
    BotCommand(command="finanzas", description="Resumen financiero del mes"),
    BotCommand(command="exportar", description="Exportar datos a Excel"),
    BotCommand(command="limpiar", description="Limpiar historial de conversación"),
    BotCommand(command="backup", description="Generar respaldo ZIP de datos"),
    BotCommand(command="modo_voz", description="Activar/desactivar respuestas por voz"),
]
