"""
Support Warteraum Cog für RedBot

Dieser Cog erkennt, wenn ein Nutzer einen Support-Warteraum betritt oder verlässt
und sendet eine Nachricht in einem konfigurierten Text-Channel.

Installation:
1. Kopiere den gesamten 'supportcog' Ordner in deinen RedBot cogs Ordner
   (normalerweise ~/.local/share/Red-DiscordBot/data/[DEIN_BOT_NAME]/cogs/)
2. Lade den Cog mit: [p]load supportcog
3. Konfiguriere mit:
   - [p]supportset channel #textchannel  (Setzt den Text-Channel für Benachrichtigungen)
   - [p]supportset room @VoiceChannel    (Setzt den Voice-Warteraum)
   - [p]supportset role @Rolle           (Setzt die Rolle, die gepingt wird)

Nutzung:
Wenn jemand den konfigurierten Voice-Channel betritt, wird automatisch
eine Nachricht im Text-Channel gesendet, die die konfigurierte Rolle pingt.
"""

import discord
from redbot.core import commands, checks, Config
from redbot.core.bot import Red


class SupportCog(commands.Cog):
    """Cog für Support-Warteraum Benachrichtigungen"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=12345678901234567890)
        
        default_guild_settings = {
            "channel": None,  # Text-Channel ID für Benachrichtigungen
            "room": None,     # Voice-Channel ID des Warteraums
            "role": None,     # Rolle ID die gepingt wird
            "message_template": "{user} ist im Support Warteraum.",  # Nachrichten-Vorlage
            "enabled": True   # Ob der Cog aktiv ist
        }
        
        self.config.register_guild(**default_guild_settings)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Hört auf Voice-Channel Änderungen und sendet Benachrichtigungen"""
        
        # Ignoriere Bots
        if member.bot:
            return
        
        guild = member.guild
        if not guild:
            return
        
        # Prüfen ob Cog für dieses Guild aktiviert ist
        enabled = await self.config.guild(guild).enabled()
        if not enabled:
            return
        
        # Hole Konfiguration
        room_id = await self.config.guild(guild).room()
        channel_id = await self.config.guild(guild).channel()
        role_id = await self.config.guild(guild).role()
        message_template = await self.config.guild(guild).message_template()
        
        # Prüfen ob alle erforderlichen Einstellungen gesetzt sind
        if not all([room_id, channel_id, role_id]):
            return
        
        # Prüfen ob es der konfigurierte Warteraum ist
        if after.channel and after.channel.id != room_id:
            return
        if before.channel and before.channel.id == room_id:
            # User hat den Warteraum verlassen - keine Aktion nötig
            return
        
        # User hat den Warteraum betreten
        try:
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return
            
            role = guild.get_role(role_id)
            if not role:
                return
            
            # Erstelle die Nachricht
            user_mention = member.mention
            role_mention = role.mention
            
            message = message_template.format(
                user=user_mention,
                user_name=member.display_name,
                role=role_mention,
                role_name=role.name
            )
            
            # Sende die Nachricht
            await channel.send(message)
            
        except discord.Forbidden:
            # Bot hat keine Berechtigung zum Senden
            pass
        except Exception as e:
            # Logge Fehler stillschweigend
            print(f"Fehler in SupportCog: {e}")

    @commands.group(name="supportset", aliases=["supportconfig"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def supportset(self, ctx: commands.Context):
        """Konfiguriere den Support Warteraum Cog"""
        pass

    @supportset.command(name="channel")
    async def supportset_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Setze den Text-Channel für Support-Benachrichtigungen"""
        await self.config.guild(ctx.guild).channel.set(channel.id)
        await ctx.send(f"✅ Text-Channel auf {channel.mention} gesetzt.")

    @supportset.command(name="room")
    async def supportset_room(self, ctx: commands.Context, room: discord.VoiceChannel):
        """Setze den Voice-Channel als Support-Warteraum"""
        await self.config.guild(ctx.guild).room.set(room.id)
        await ctx.send(f"✅ Voice-Warteraum auf {room.mention} gesetzt.")

    @supportset.command(name="role")
    async def supportset_role(self, ctx: commands.Context, role: discord.Role):
        """Setze die Rolle, die bei neuen Nutzern im Warteraum gepingt wird"""
        await self.config.guild(ctx.guild).role.set(role.id)
        await ctx.send(f"✅ Support-Rolle auf {role.mention} gesetzt.")

    @supportset.command(name="message")
    async def supportset_message(self, ctx: commands.Context, *, template: str):
        """
        Setze die Nachrichten-Vorlage.
        
        Verfügbare Platzhalter:
        {user} - Erwähnt den Nutzer
        {user_name} - Name des Nutzers
        {role} - Erwähnt die Rolle
        {role_name} - Name der Rolle
        
        Beispiel: `{user} wartet im Support Warteraum auf Hilfe von {role}`
        """
        await self.config.guild(ctx.guild).message_template.set(template)
        await ctx.send(f"✅ Nachrichten-Vorlage aktualisiert.")

    @supportset.command(name="toggle")
    async def supportset_toggle(self, ctx: commands.Context):
        """Aktiviere oder deaktiviere den Support Cog für diesen Server"""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        status = "aktiviert" if not current else "deaktiviert"
        await ctx.send(f"✅ Support Cog {status}.")

    @supportset.command(name="show")
    async def supportset_show(self, ctx: commands.Context):
        """Zeige die aktuelle Konfiguration"""
        guild_data = await self.config.guild(ctx.guild).all()
        
        channel_id = guild_data.get("channel")
        room_id = guild_data.get("room")
        role_id = guild_data.get("role")
        template = guild_data.get("message_template")
        enabled = guild_data.get("enabled")
        
        channel_mention = f"<#{channel_id}>" if channel_id else "❌ Nicht gesetzt"
        room_mention = f"<#{room_id}>" if room_id else "❌ Nicht gesetzt"
        role_mention = f"<@&{role_id}>" if role_id else "❌ Nicht gesetzt"
        status = "✅ Aktiv" if enabled else "❌ Deaktiviert"
        
        embed = discord.Embed(
            title="🛠️ Support Warteraum Konfiguration",
            color=discord.Color.blue()
        )
        embed.add_field(name="Status", value=status, inline=False)
        embed.add_field(name="Text-Channel", value=channel_mention, inline=True)
        embed.add_field(name="Voice-Warteraum", value=room_mention, inline=True)
        embed.add_field(name="Support-Rolle", value=role_mention, inline=True)
        embed.add_field(name="Nachrichten-Vorlage", value=f"`{template}`", inline=False)
        
        await ctx.send(embed=embed)


async def setup(bot: Red):
    """Lädt den Cog"""
    await bot.add_cog(SupportCog(bot))


async def teardown(bot: Red):
    """Entfernt den Cog"""
    await bot.remove_cog("SupportCog")
