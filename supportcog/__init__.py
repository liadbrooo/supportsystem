"""
Support Warteraum Cog für RedBot

Dieser Cog erkennt, wenn ein Nutzer einen Support-Warteraum betritt oder verlässt
und sendet eine Nachricht in einem konfigurierten Text-Channel.

Installation:
1. Kopiere den gesamten 'supportcog' Ordner in deinen RedBot cogs Ordner
   (normalisieren ~/.local/share/Red-DiscordBot/data/[DEIN_BOT_NAME]/cogs/)
2. Lade den Cog mit: [p]load supportcog
3. Konfiguriere mit:
   - [p]supportset channel #textchannel  (Setzt den Text-Channel für Benachrichtigungen)
   - [p]supportset room @VoiceChannel    (Setzt den Voice-Warteraum)
   - [p]supportset role @Rolle           (Setzt die Rolle, die gepingt wird)

Nutzung:
Wenn jemand den konfigurierten Voice-Channel betritt, wird automatisch
ein schönes Embed im Text-Channel gesendet, das die konfigurierte Rolle pingt.
"""

import discord
from redbot.core import commands, checks, Config
from redbot.core.bot import Red
from datetime import datetime


class SupportCog(commands.Cog):
    """Cog für Support-Warteraum Benachrichtigungen"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=12345678901234567890)
        
        default_guild_settings = {
            "channel": None,  # Text-Channel ID für Benachrichtigungen
            "room": None,     # Voice-Channel ID des Warteraums
            "role": None,     # Rolle ID die gepingt wird
            "use_embed": True,  # Ob Embeds verwendet werden sollen
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
        use_embed = await self.config.guild(guild).use_embed()
        
        # Prüfen ob alle erforderlichen Einstellungen gesetzt sind
        if not all([room_id, channel_id, role_id]):
            return
        
        # Prüfen ob es der konfigurierte Warteraum ist
        # User muss DEN Warteraum BETRETEN (vorher woanders oder offline, jetzt im Warteraum)
        if after.channel is None or after.channel.id != room_id:
            return
        if before.channel is not None and before.channel.id == room_id:
            # User war bereits im Warteraum - keine Aktion
            return
        
        # User hat den Warteraum soeben betreten
        try:
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return
            
            role = guild.get_role(role_id)
            if not role:
                return
            
            # Baue die Nachricht mit korrektem Role-Ping
            # Wichtig: Role-mention muss als roher String gesendet werden
            role_mention = f"<@&{role_id}>"
            user_mention = member.mention
            user_avatar = member.display_avatar.url
            
            if use_embed:
                # Erstelle ein schönes Embed
                embed = discord.Embed(
                    title="🎧 Neuer Support-Anfrage",
                    description=f"{user_mention} hat den Support-Warteraum betreten und wartet auf Hilfe!",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow()
                )
                
                embed.set_thumbnail(url=user_avatar)
                embed.add_field(
                    name="👤 Nutzer",
                    value=f"{user_mention}\n(`{member.display_name}`)",
                    inline=True
                )
                embed.add_field(
                    name="🔔 Support Team",
                    value=f"{role_mention}",
                    inline=True
                )
                embed.add_field(
                    name="📍 Channel",
                    value=f"{after.channel.mention}",
                    inline=True
                )
                embed.set_footer(text="Support Warteraum System")
                
                # Sende das Embed mit zusätzlichem Role-Ping im Content
                await channel.send(content=f"{role_mention}", embed=embed)
            else:
                # Einfache Textnachricht (Fallback)
                message = f"🎧 {role_mention} | {user_mention} (`{member.display_name}`) ist im Support-Warteraum ({after.channel.mention})"
                await channel.send(message)
            
        except discord.Forbidden:
            # Bot hat keine Berechtigung zum Senden
            pass
        except Exception as e:
            # Logge Fehler
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

    @supportset.command(name="embed")
    async def supportset_embed(self, ctx: commands.Context, enabled: bool = None):
        """
        Aktiviere oder deaktiviere Embed-Nachrichten.
        
        Embeds sehen besser aus und zeigen mehr Informationen.
        Ohne Parameter wird der aktuelle Status umgeschaltet.
        """
        if enabled is None:
            current = await self.config.guild(ctx.guild).use_embed()
            await self.config.guild(ctx.guild).use_embed.set(not current)
            status = "aktiviert" if not current else "deaktiviert"
        else:
            await self.config.guild(ctx.guild).use_embed.set(enabled)
            status = "aktiviert" if enabled else "deaktiviert"
        
        await ctx.send(f"✅ Embed-Nachrichten {status}.")

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
        use_embed = guild_data.get("use_embed", True)
        enabled = guild_data.get("enabled")
        
        channel_mention = f"<#{channel_id}>" if channel_id else "❌ Nicht gesetzt"
        room_mention = f"<#{room_id}>" if room_id else "❌ Nicht gesetzt"
        role_mention = f"<@&{role_id}>" if role_id else "❌ Nicht gesetzt"
        embed_status = "✅ Aktiv" if use_embed else "❌ Deaktiviert"
        cog_status = "✅ Aktiv" if enabled else "❌ Deaktiviert"
        
        embed = discord.Embed(
            title="🛠️ Support Warteraum Konfiguration",
            color=discord.Color.blue()
        )
        embed.add_field(name="Cog Status", value=cog_status, inline=False)
        embed.add_field(name="Embeds", value=embed_status, inline=True)
        embed.add_field(name="Text-Channel", value=channel_mention, inline=True)
        embed.add_field(name="Voice-Warteraum", value=room_mention, inline=True)
        embed.add_field(name="Support-Rolle", value=role_mention, inline=True)
        
        await ctx.send(embed=embed)


async def setup(bot: Red):
    """Lädt den Cog"""
    await bot.add_cog(SupportCog(bot))


async def teardown(bot: Red):
    """Entfernt den Cog"""
    await bot.remove_cog("SupportCog")
