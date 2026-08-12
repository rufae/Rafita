"""End-to-end tests for Telegram bot flow with mocked dependencies."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, User, Chat, Bot
from telegram.ext import CallbackContext

from src.handlers.chat import handle_message


@pytest.fixture
def mock_bot():
    """Create a mock bot."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    return bot


@pytest.fixture
def mock_telegram_update(mock_bot):
    """Create a mock Telegram update with bot attached."""
    user = User(id=12345, first_name="Test", is_bot=False, username="testuser")
    chat = Chat(id=12345, type="private")
    message = Message(
        message_id=1,
        date=None,
        chat=chat,
        from_user=user,
        text="Hola, ¿qué sabes de mí?"
    )
    # Set bot after creation
    message._bot = mock_bot
    update = Update(update_id=1, message=message)
    return update


@pytest.fixture
def mock_context(mock_bot):
    """Create a mock callback context."""
    context = MagicMock(spec=CallbackContext)
    context.bot = mock_bot
    context.user_data = {}
    return context


@pytest.mark.asyncio
async def test_handle_message_basic_flow(mock_bot, mock_context):
    """Test basic message handling flow without tool invocation."""
    # Use a simple greeting that doesn't trigger tool intent
    user = User(id=12345, first_name="Test", is_bot=False)
    chat = Chat(id=12345, type="private")
    message = Message(
        message_id=1,
        date=None,
        chat=chat,
        from_user=user,
        text="Hola"
    )
    message._bot = mock_bot
    update = Update(update_id=1, message=message)
    
    with patch('src.handlers.chat.db') as mock_db, \
         patch('src.handlers.chat.llm') as mock_llm:
        
        # Mock database
        mock_db.save_chat_message = AsyncMock()
        mock_db.get_chat_history = AsyncMock(return_value=[])
        
        # Mock LLM to return a simple response without tools
        mock_llm.chat = AsyncMock(return_value="Hola! Soy Rafita, tu asistente virtual.")
        
        # Call the handler
        await handle_message(update, mock_context)
        
        # Verify the LLM was called
        mock_llm.chat.assert_called_once()
        
        # Verify the response was sent
        mock_context.bot.send_message.assert_called_once()
        call_args = mock_context.bot.send_message.call_args
        assert call_args[1]['chat_id'] == 12345
        assert "Rafita" in call_args[1]['text']


@pytest.mark.asyncio
async def test_handle_message_with_tool_invocation(mock_telegram_update, mock_context):
    """Test message handling when tools are invoked."""
    with patch('src.handlers.chat.db') as mock_db, \
         patch('src.handlers.chat.llm') as mock_llm, \
         patch('src.handlers.chat._execute_tool') as mock_execute_tool, \
         patch('src.handlers.chat.vector_db') as mock_vector_db:
        
        # Mock database
        mock_db.save_chat_message = AsyncMock()
        mock_db.get_chat_history = AsyncMock(return_value=[])
        mock_db.log_second_brain_query = AsyncMock()
        
        # Mock vector DB query
        mock_vector_db.query = AsyncMock(return_value={
            "results": [{"relevance": 0.8}],
            "notes_found": ["Audi A3.md"]
        })
        
        # Mock LLM to return a tool call
        tool_call = {
            "id": "call_1",
            "function": {
                "name": "search_second_brain",
                "arguments": '{"query": "información personal"}'
            }
        }
        mock_llm.chat_with_tools = AsyncMock(return_value=("", [tool_call]))
        
        # Mock tool execution with correct structure
        mock_execute_tool.return_value = {
            "success": True,
            "message": "Encontré información sobre tu coche Audi A3"
        }
        
        await handle_message(mock_telegram_update, mock_context)
        
        # Verify the LLM was called once with tools
        assert mock_llm.chat_with_tools.call_count == 1
        
        # Verify tool was executed
        mock_execute_tool.assert_called_once()
        
        # Verify vector DB was queried
        mock_vector_db.query.assert_called_once()
        
        # Verify response was sent
        mock_context.bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_empty_text(mock_bot, mock_context):
    """Test handling of empty message."""
    user = User(id=12345, first_name="Test", is_bot=False)
    chat = Chat(id=12345, type="private")
    message = Message(message_id=1, date=None, chat=chat, from_user=user, text="")
    message._bot = mock_bot
    update = Update(update_id=1, message=message)
    
    await handle_message(update, mock_context)
    
    # Should not call orchestrator or send message
    mock_context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_error_handling(mock_telegram_update, mock_context):
    """Test error handling in message flow."""
    with patch('src.handlers.chat.db') as mock_db, \
         patch('src.handlers.chat.llm') as mock_llm:
        
        # Mock database
        mock_db.save_chat_message = AsyncMock()
        mock_db.get_chat_history = AsyncMock(return_value=[])
        
        # Mock LLM to raise an error
        mock_llm.chat = AsyncMock(side_effect=Exception("Test error"))
        
        await handle_message(mock_telegram_update, mock_context)
        
        # Should send error message
        mock_context.bot.send_message.assert_called_once()
        call_args = mock_context.bot.send_message.call_args
        assert "Error" in call_args[1]['text'] or "error" in call_args[1]['text']


@pytest.mark.asyncio
async def test_handle_message_long_conversation(mock_bot, mock_context):
    """Test handling of messages in a long conversation."""
    with patch('src.handlers.chat.db') as mock_db, \
         patch('src.handlers.chat.llm') as mock_llm:
        
        # Mock conversation history
        mock_db.save_chat_message = AsyncMock()
        mock_db.get_chat_history = AsyncMock(return_value=[
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "Hola! ¿En qué puedo ayudarte?"},
            {"role": "user", "content": "¿Qué sabes de mi coche?"},
            {"role": "assistant", "content": "Tienes un Audi A3."},
        ])
        
        user = User(id=12345, first_name="Test", is_bot=False)
        chat = Chat(id=12345, type="private")
        message = Message(
            message_id=5,
            date=None,
            chat=chat,
            from_user=user,
            text="¿Y de mi salud?"
        )
        message._bot = mock_bot
        update = Update(update_id=5, message=message)
        
        # Mock LLM response
        mock_llm.chat = AsyncMock(return_value="Según mis notas, tu salud está bien.")
        
        await handle_message(update, mock_context)
        
        # Verify orchestrator was called with conversation context
        assert mock_llm.chat.called
        
        # Verify response was sent
        mock_context.bot.send_message.assert_called_once()
