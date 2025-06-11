import pytest
import unittest.mock as mock
import sys
import os
import discord

# Add the parent directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from commands import BotCommands # Assuming BotCommands is the Cog class
from constants import EMBED_COLOR_INFO # For checking embed color

# Mock discord.Interaction and its components as realistically as needed
class AsyncMock(mock.MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)

@pytest.fixture
def mock_interaction():
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock(spec=discord.InteractionResponse)
    interaction.user = mock.Mock(spec=discord.User)
    interaction.user.id = 123
    interaction.guild = mock.Mock(spec=discord.Guild)
    interaction.guild.id = 456
    return interaction

@pytest.fixture
def mock_member():
    member = mock.Mock(spec=discord.Member)
    member.id = 78901
    member.display_name = "Test User"
    member.display_avatar = mock.Mock(spec=discord.Asset)
    member.display_avatar.url = "http://example.com/avatar.png"
    return member

@pytest.fixture
def bot_commands_instance():
    # Mock the bot and other dependencies for BotCommands if necessary
    mock_bot = AsyncMock()
    mock_sleep_check_manager = AsyncMock()
    mock_voice_state_manager = AsyncMock()
    return BotCommands(bot=mock_bot, sleep_check_manager=mock_sleep_check_manager, voice_state_manager=mock_voice_state_manager)

@pytest.mark.asyncio
@mock.patch('commands.get_mute_count') # Patch where it's used in commands.py
async def test_get_mute_count_callback_with_count(mock_db_get_mute_count, bot_commands_instance, mock_interaction, mock_member):
    """Test the /get_mute_count command when the user has a mute count."""
    mock_db_get_mute_count.return_value = 5 # User has been muted 5 times

    await bot_commands_instance.get_mute_count_callback.callback(bot_commands_instance, mock_interaction, mock_member)

    mock_interaction.response.send_message.assert_called_once()
    args, kwargs = mock_interaction.response.send_message.call_args

    assert kwargs['ephemeral'] is True
    embed = kwargs['embed']

    assert isinstance(embed, discord.Embed)
    assert embed.author.name == "Test User"
    assert embed.author.icon_url == "http://example.com/avatar.png"
    assert embed.description == "自動ミュートされた回数: 5 回"
    assert embed.color.value == EMBED_COLOR_INFO
    mock_db_get_mute_count.assert_called_once_with(mock_member.id)

@pytest.mark.asyncio
@mock.patch('commands.get_mute_count') # Patch where it's used in commands.py
async def test_get_mute_count_callback_no_count(mock_db_get_mute_count, bot_commands_instance, mock_interaction, mock_member):
    """Test the /get_mute_count command when the user has no mute count."""
    mock_db_get_mute_count.return_value = 0 # User has not been muted

    await bot_commands_instance.get_mute_count_callback.callback(bot_commands_instance, mock_interaction, mock_member)

    mock_interaction.response.send_message.assert_called_once()
    args, kwargs = mock_interaction.response.send_message.call_args

    assert kwargs['ephemeral'] is True
    embed = kwargs['embed']

    assert isinstance(embed, discord.Embed)
    assert embed.author.name == "Test User"
    assert embed.author.icon_url == "http://example.com/avatar.png"
    assert embed.description == "まだ自動ミュートされたことはありません。"
    assert embed.color.value == EMBED_COLOR_INFO
    mock_db_get_mute_count.assert_called_once_with(mock_member.id)

@pytest.mark.asyncio
@mock.patch('commands.get_mute_count') # Patch where it's used in commands.py
async def test_get_mute_count_callback_db_error(mock_db_get_mute_count, bot_commands_instance, mock_interaction, mock_member):
    """Test the /get_mute_count command when the database lookup fails."""
    mock_db_get_mute_count.side_effect = Exception("Database boom!")

    await bot_commands_instance.get_mute_count_callback.callback(bot_commands_instance, mock_interaction, mock_member)

    mock_interaction.response.send_message.assert_called_once_with(
        "ミュート回数の取得中にエラーが発生しました。",
        ephemeral=True
    )
    mock_db_get_mute_count.assert_called_once_with(mock_member.id)

# It's good practice to ensure that discord.Embed is properly mocked if not already handled by AsyncMock
# For these tests, we are checking the instance of the created embed, so direct mocking of the class
# might only be needed if we want to assert how discord.Embed itself was called.
# The current approach of checking the output embed's attributes is generally sufficient.
