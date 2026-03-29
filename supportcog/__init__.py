"""
Support Warteraum Cog für RedBot mit On-Duty System

Dieser Cog erkennt, wenn ein Nutzer einen Support-Warteraum betritt oder verlässt
und sendet eine Nachricht in einem konfigurierten Text-Channel.
Enthält ein On-Duty System für Support-Teammitglieder, Feedback-System und Support-Alarm.

Installation:
1. Kopiere den gesamten 'supportcog' Ordner in deinen RedBot cogs Ordner
   (normalerweise ~/.local/share/Red-DiscordBot/data/[DEIN_BOT_NAME]/cogs/)
2. Lade den Cog mit: [p]load supportcog
3. Konfiguriere mit:
   - [p]supportset channel #textchannel ODER Channel-ID  (Setzt den Text-Channel für Benachrichtigungen)
   - [p]supportset room @VoiceChannel ODER Voice-Channel-ID  (Setzt den Voice-Warteraum)
   - [p]supportset role @Rolle ODER Rollen-ID  (Setzt die Basis-Supportrolle)
   - [p]supportset feedbackchannel #channel  (Setzt den Channel für Feedback-Logs)
   - [p]supportset supportcallchannel #channel  (Setzt den Channel für Supportrufe)
   - [p]supportset supportcallrole @Rolle  (Setzt die Rolle die bei Supportrufen gepingt wird)
   - [p]supportset autoduty <Minuten>  (Setzt die Zeit bis zur automatischen Duty-Abmeldung)
   - ODER verwende [p]supportset setup für einen interaktiven Einrichtungsassistenten

Nutzung:
- Wenn jemand den konfigurierten Voice-Channel betritt, wird automatisch
  eine schöne Nachricht im Text-Channel gesendet.
- Support-Teamler können sich mit Buttons an- und abmelden
- Nur Teamler mit der "On Duty" Rolle werden gepingt!
- Feedback-Panel mit `[p]feedback panel` erstellen
- Support-Alarm-Panel mit `[p]supportcall panel` erstellen
"""

import discord
from redbot.core import commands, checks, Config
from redbot.core.bot import Red
from datetime import datetime, timedelta
from typing import Optional
import asyncio
import re


class SupportCog(commands.Cog):
    """Cog für Support-Warteraum Benachrichtigungen"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=12345678901234567890)
        
        default_guild_settings = {
            "channel": None,  # Text-Channel ID für Benachrichtigungen
            "room": None,     # Voice-Channel ID des Warteraums
            "role": None,     # Rolle ID die gepingt wird (Basis-Supportrolle)
            "duty_role": None,  # Automatisch erstellte Duty-Rolle ID
            "use_embed": True,  # Ob Embeds verwendet werden sollen
            "enabled": True,   # Ob der Cog aktiv ist
            "duty_channel": None,  # Channel für Duty-Nachrichten
            "auto_remove_duty": True,  # Automatisch Duty entfernen nach X Stunden
            "duty_timeout": 4,  # Stunden nach denen Duty automatisch entfernt wird
            "feedback_channel": None,  # Channel für Feedback Logs
            "feedback_enabled": True,  # Ob Feedback System aktiv ist
            "supportcall_channel": None,  # Channel für Supportrufe
            "supportcall_role": None,  # Rolle die bei Supportruf gepingt wird
            "autoduty_minutes": 60  # Minuten bis zur automatischen Duty-Abmeldung
        }
        
        # Speichert On-Duty Status pro User
        default_member_settings = {
            "on_duty": False,
            "duty_start": None
        }
        
        self.config.register_guild(**default_guild_settings)
        self.config.register_member(**default_member_settings)
        
        # Cache für aktive Duty-User (wird bei Bot-Start neu aufgebaut)
        self.duty_cache = {}
        
        # Speichere View-Message IDs für Persistence
        self.duty_message_ids = {}

    async def get_or_create_duty_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        """Erstellt oder holt die Duty-Rolle für den Server"""
        duty_role_id = await self.config.guild(guild).duty_role()
        
        if duty_role_id:
            role = guild.get_role(duty_role_id)
            if role:
                return role
        
        # Rolle existiert nicht mehr, erstelle neue
        try:
            new_role = await guild.create_role(
                name="🟢 On Duty",
                color=discord.Color.green(),
                mentionable=True,
                reason="Auto-erstellte Duty-Rolle für Support-System"
            )
            await self.config.guild(guild).duty_role.set(new_role.id)
            return new_role
        except discord.Forbidden:
            return None
    
    async def add_duty_role(self, member: discord.Member):
        """Fügt einem Member die Duty-Rolle hinzu"""
        duty_role = await self.get_or_create_duty_role(member.guild)
        if duty_role and duty_role not in member.roles:
            try:
                await member.add_roles(duty_role, reason="Support-Duty aktiv")
            except discord.Forbidden:
                pass
    
    async def remove_duty_role(self, member: discord.Member):
        """Entfernt die Duty-Rolle von einem Member"""
        duty_role = await self.get_or_create_duty_role(member.guild)
        if duty_role and duty_role in member.roles:
            try:
                await member.remove_roles(duty_role, reason="Support-Duty beendet")
            except discord.Forbidden:
                pass

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
        duty_channel_id = await self.config.guild(guild).duty_channel()
        
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
            
            base_role = guild.get_role(role_id)
            if not base_role:
                return
            
            # Hole alle On-Duty User mit der Support-Rolle
            duty_mentions = []
            duty_members = []
            
            for m in base_role.members:
                is_on_duty = await self.config.member(m).on_duty()
                if is_on_duty:
                    duty_mentions.append(f"<@{m.id}>")
                    duty_members.append(m)
            
            user_mention = member.mention
            user_avatar = member.display_avatar.url
            
            # Bestimme den Channel für die Nachricht - IMMER support channel!
            notify_channel = channel
            
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
                
                if duty_members:
                    # Zeige nur On-Duty Teamler an
                    duty_list = "\n".join([f"• {m.display_name}" for m in duty_members[:5]])
                    if len(duty_members) > 5:
                        duty_list += f"\n• ...und {len(duty_members) - 5} weitere"
                    embed.add_field(
                        name="🟢 Verfügbare Supporter",
                        value=duty_list,
                        inline=True
                    )
                    # Pinge alle On-Duty User
                    ping_content = " ".join(duty_mentions)
                else:
                    embed.add_field(
                        name="🔴 Keine Supporter verfügbar",
                        value=f"Niemand ist gerade im Dienst! {base_role.mention}",
                        inline=True
                    )
                    # Fallback: Pinge die Basis-Rolle wenn niemand Duty hat
                    ping_content = f"{base_role.mention}"
                
                embed.add_field(
                    name="📍 Channel",
                    value=f"{after.channel.mention}",
                    inline=True
                )
                embed.set_footer(text="Support Warteraum System • On-Duty aktiv")
                
                # Sende das Embed mit Role-Ping im Content
                await notify_channel.send(content=ping_content, embed=embed)
            else:
                # Einfache Textnachricht (Fallback)
                if duty_members:
                    ping_content = " ".join(duty_mentions)
                    message = f"🎧 {ping_content} | {user_mention} (`{member.display_name}`) ist im Support-Warteraum ({after.channel.mention})"
                else:
                    message = f"🎧 {base_role.mention} | {user_mention} (`{member.display_name}`) ist im Support-Warteraum ({after.channel.mention}) - Niemand im Duty!"
                await notify_channel.send(message)
            
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
    async def supportset_channel(self, ctx: commands.Context, channel: str):
        """Setze den Text-Channel für Support-Benachrichtigungen (Mention oder ID)"""
        # Versuche Channel zu finden - entweder durch Mention oder ID
        channel_obj = None
        
        # Prüfe ob es eine Mention ist (#channel oder <#123456789>)
        if channel.startswith("<#") and channel.endswith(">"):
            channel_id = int(channel[2:-1])
            channel_obj = ctx.guild.get_channel(channel_id)
        elif channel.isdigit():
            # Es ist eine ID
            channel_obj = ctx.guild.get_channel(int(channel))
        else:
            # Versuche über channel_mentions falls User einfach #channel geschrieben hat
            if ctx.message.channel_mentions:
                channel_obj = ctx.message.channel_mentions[0]
        
        if not channel_obj or not isinstance(channel_obj, discord.TextChannel):
            await ctx.send("❌ Bitte erwähne einen gültigen Text-Channel mit # oder gib die Channel-ID ein!")
            return
        
        await self.config.guild(ctx.guild).channel.set(channel_obj.id)
        await ctx.send(f"✅ Text-Channel auf {channel_obj.mention} gesetzt.")

    @supportset.command(name="room")
    async def supportset_room(self, ctx: commands.Context, room: str):
        """Setze den Voice-Channel als Support-Warteraum (Mention oder ID)"""
        # Versuche Voice Channel zu finden - entweder durch Mention oder ID
        room_obj = None
        
        # Prüfe ob es eine Mention ist (<#123456789>)
        if room.startswith("<#") and room.endswith(">"):
            channel_id = int(room[2:-1])
            room_obj = ctx.guild.get_channel(channel_id)
        elif room.isdigit():
            # Es ist eine ID
            room_obj = ctx.guild.get_channel(int(room))
        else:
            # Versuche über channel_mentions
            if ctx.message.channel_mentions:
                room_obj = ctx.message.channel_mentions[0]
        
        if not room_obj or not isinstance(room_obj, discord.VoiceChannel):
            await ctx.send("❌ Bitte erwähne einen gültigen Voice-Channel mit # oder gib die Channel-ID ein!")
            return
        
        await self.config.guild(ctx.guild).room.set(room_obj.id)
        await ctx.send(f"✅ Voice-Warteraum auf {room_obj.mention} gesetzt.")

    @supportset.command(name="role")
    async def supportset_role(self, ctx: commands.Context, role: str):
        """Setze die Basis-Supportrolle (Mention oder ID)"""
        # Versuche Rolle zu finden - entweder durch Mention oder ID
        role_obj = None
        
        # Prüfe ob es eine Mention ist (@Rolle oder <@&123456789>)
        if role.startswith("<@&") and role.endswith(">"):
            role_id = int(role[3:-1])
            role_obj = ctx.guild.get_role(role_id)
        elif role.isdigit():
            # Es ist eine ID
            role_obj = ctx.guild.get_role(int(role))
        else:
            # Versuche über role_mentions
            if ctx.message.role_mentions:
                role_obj = ctx.message.role_mentions[0]
        
        if not role_obj:
            await ctx.send("❌ Bitte erwähne eine gültige Rolle mit @ oder gib die Rollen-ID ein!")
            return
        
        await self.config.guild(ctx.guild).role.set(role_obj.id)
        await ctx.send(f"✅ Support-Rolle auf {role_obj.mention} gesetzt.")

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
        duty_channel_id = guild_data.get("duty_channel")
        auto_duty = guild_data.get("auto_remove_duty", True)
        duty_timeout = guild_data.get("duty_timeout", 4)
        
        channel_mention = f"<#{channel_id}>" if channel_id else "❌ Nicht gesetzt"
        room_mention = f"<#{room_id}>" if room_id else "❌ Nicht gesetzt"
        role_mention = f"<@&{role_id}>" if role_id else "❌ Nicht gesetzt"
        duty_channel_mention = f"<#{duty_channel_id}>" if duty_channel_id else "Gleicher wie Support-Channel"
        embed_status = "✅ Aktiv" if use_embed else "❌ Deaktiviert"
        cog_status = "✅ Aktiv" if enabled else "❌ Deaktiviert"
        auto_duty_status = f"✅ Aktiv ({duty_timeout}h)" if auto_duty else "❌ Deaktiviert"
        
        # Zähle aktive Duty-User
        duty_count = 0
        if role_id:
            base_role = ctx.guild.get_role(role_id)
            if base_role:
                for m in base_role.members:
                    is_on_duty = await self.config.member(m).on_duty()
                    if is_on_duty:
                        duty_count += 1
        
        embed = discord.Embed(
            title="🛠️ Support Warteraum Konfiguration",
            color=discord.Color.blue()
        )
        embed.add_field(name="Cog Status", value=cog_status, inline=False)
        embed.add_field(name="Embeds", value=embed_status, inline=True)
        embed.add_field(name="Aktive Duty", value=f"🟢 {duty_count} Supporter", inline=True)
        embed.add_field(name="Auto-Duty-Ende", value=auto_duty_status, inline=True)
        embed.add_field(name="Text-Channel", value=channel_mention, inline=True)
        embed.add_field(name="Voice-Warteraum", value=room_mention, inline=True)
        embed.add_field(name="Support-Basisrolle", value=role_mention, inline=True)
        embed.add_field(name="Duty-Log-Channel", value=duty_channel_mention, inline=True)
        
        await ctx.send(embed=embed)

    @supportset.command(name="setup")
    async def supportset_setup(self, ctx: commands.Context):
        """
        Interaktiver Einrichtungsassistent für den Support Cog.
        Führt dich Schritt für Schritt durch die Einrichtung.
        """
        await ctx.send("🔧 **Willkommen beim Support-Cog Einrichtungsassistenten!**\n\nIch werde dich jetzt durch die Einrichtung führen. Bitte antworte auf die folgenden Fragen.")
        
        questions = [
            ("1️⃣ Welcher **Text-Channel** soll für Support-Benachrichtigungen genutzt werden?", "channel", discord.TextChannel),
            ("2️⃣ Welcher **Voice-Channel** ist der Support-Warteraum?", "room", discord.VoiceChannel),
            ("3️⃣ Welche **Rolle** ist die Basis-Supportrolle? (Mitglieder dieser Rolle können sich auf Duty setzen)", "role", discord.Role),
            ("4️⃣ (Optional) In welchem Channel sollen **Duty-Nachrichten** erscheinen? (Antworte mit 'skip' zum Überspringen)", "duty_channel", discord.TextChannel, True),
        ]
        
        answers = {}
        
        for question_data in questions:
            optional = len(question_data) > 3 and question_data[3]
            
            embed = discord.Embed(
                title=question_data[0],
                description="Sende deine Antwort als Nachricht hier im Channel.\n• Erwähne den Channel/die Rolle einfach mit @ oder #\n• Bei Frage 4 kannst du 'skip' schreiben um zu überspringen",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            
            # Warte auf Antwort
            try:
                def check(m):
                    return m.author == ctx.author and m.channel == ctx.channel
                
                msg = await ctx.bot.wait_for("message", timeout=60.0, check=check)
                
                if question_data[2] == discord.TextChannel or question_data[2] == discord.VoiceChannel:
                    if msg.content.lower() == "skip" and optional:
                        answers[question_data[1]] = None
                        continue
                    channels = msg.channel_mentions
                    if not channels:
                        await ctx.send("❌ Bitte erwähne einen gültigen Channel mit #!")
                        return
                    answers[question_data[1]] = channels[0]
                elif question_data[2] == discord.Role:
                    roles = msg.role_mentions
                    if not roles:
                        await ctx.send("❌ Bitte erwähne eine gültige Rolle mit @!")
                        return
                    answers[question_data[1]] = roles[0]
                    
            except asyncio.TimeoutError:
                await ctx.send("❌ Zeitüberschreitung! Bitte starte den Assistenten neu mit `[p]supportset setup`")
                return
        
        # Speichere alle Einstellungen
        await self.config.guild(ctx.guild).channel.set(answers["channel"].id)
        await self.config.guild(ctx.guild).room.set(answers["room"].id)
        await self.config.guild(ctx.guild).role.set(answers["role"].id)
        
        if answers.get("duty_channel"):
            await self.config.guild(ctx.guild).duty_channel.set(answers["duty_channel"].id)
        
        embed = discord.Embed(
            title="✅ Einrichtung erfolgreich!",
            description="Der Support-Cog ist jetzt konfiguriert und bereit!\n\n**Zusammenfassung:**\n"
                        f"• 📝 Text-Channel: {answers['channel'].mention}\n"
                        f"• 🎤 Voice-Warteraum: {answers['room'].mention}\n"
                        f"• 👥 Support-Rolle: {answers['role'].mention}",
            color=discord.Color.green()
        )
        
        if answers.get("duty_channel"):
            embed.description += f"\n• 📢 Duty-Channel: {answers['duty_channel'].mention}"
        
        embed.description += "\n\n**Nächste Schritte:**\n"
        embed.description += "• Support-Teamler können sich mit `[p]duty on` anmelden\n"
        embed.description += "• Mit `[p]duty off` wieder abmelden\n"
        embed.description += "• Mit `[p]duty status` den aktuellen Status sehen"
        
        await ctx.send(embed=embed)

    @supportset.command(name="dutychannel")
    async def supportset_dutychannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """
        Setze den Channel für Duty-Nachrichten.
        Ohne Channel-Angabe wird der normale Support-Channel verwendet.
        Verwende 'reset' um zurückzusetzen.
        """
        if channel is None:
            await self.config.guild(ctx.guild).duty_channel.set(None)
            await ctx.send("✅ Duty-Nachrichten werden jetzt im normalen Support-Channel angezeigt.")
        else:
            await self.config.guild(ctx.guild).duty_channel.set(channel.id)
            await ctx.send(f"✅ Duty-Nachrichten werden jetzt in {channel.mention} angezeigt.")

    @supportset.command(name="autoduty")
    async def supportset_autoduty(self, ctx: commands.Context, minutes: int = None):
        """
        Konfiguriere automatisches Duty-Ende nach X Minuten.
        
        - `0`: Automatisches Beenden deaktivieren
        - `5-1440`: Anzahl der Minuten nach denen Duty automatisch endet (Standard: 60)
        """
        if minutes is None or minutes <= 0:
            await self.config.guild(ctx.guild).auto_remove_duty.set(False)
            await ctx.send("✅ Automatisches Duty-Ende deaktiviert.")
        else:
            if minutes < 5:
                minutes = 5
            elif minutes > 1440:
                minutes = 1440
            await self.config.guild(ctx.guild).auto_remove_duty.set(True)
            await self.config.guild(ctx.guild).autoduty_minutes.set(minutes)
            hours = minutes / 60
            await ctx.send(f"✅ Duty wird automatisch nach {minutes} Minuten ({hours:.1f}h) beendet.")

    @supportset.command(name="feedbackchannel")
    async def supportset_feedbackchannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """
        Setze den Channel für Feedback-Logs.
        Ohne Channel-Angabe wird Feedback deaktiviert.
        """
        if channel is None:
            await self.config.guild(ctx.guild).feedback_channel.set(None)
            await self.config.guild(ctx.guild).feedback_enabled.set(False)
            await ctx.send("✅ Feedback-System deaktiviert.")
        else:
            await self.config.guild(ctx.guild).feedback_channel.set(channel.id)
            await self.config.guild(ctx.guild).feedback_enabled.set(True)
            await ctx.send(f"✅ Feedback wird jetzt in {channel.mention} geloggt.")

    @supportset.command(name="supportcallchannel")
    async def supportset_supportcallchannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """
        Setze den Channel für Supportrufe.
        Ohne Channel-Angabe wird der normale Support-Channel verwendet.
        """
        if channel is None:
            await self.config.guild(ctx.guild).supportcall_channel.set(None)
            await ctx.send("✅ Supportrufe werden im normalen Support-Channel angezeigt.")
        else:
            await self.config.guild(ctx.guild).supportcall_channel.set(channel.id)
            await ctx.send(f"✅ Supportrufe werden jetzt in {channel.mention} angezeigt.")

    @supportset.command(name="supportcallrole")
    async def supportset_supportcallrole(self, ctx: commands.Context, role: str):
        """
        Setze die Rolle die bei Supportrufen gepingt wird (Mention oder ID).
        """
        role_obj = None
        
        if role.startswith("<@&") and role.endswith(">"):
            role_id = int(role[3:-1])
            role_obj = ctx.guild.get_role(role_id)
        elif role.isdigit():
            role_obj = ctx.guild.get_role(int(role))
        else:
            if ctx.message.role_mentions:
                role_obj = ctx.message.role_mentions[0]
        
        if not role_obj:
            await ctx.send("❌ Bitte erwähne eine gültige Rolle mit @ oder gib die Rollen-ID ein!")
            return
        
        await self.config.guild(ctx.guild).supportcall_role.set(role_obj.id)
        await ctx.send(f"✅ Support-Ruf Rolle auf {role_obj.mention} gesetzt.")

    # ============================================
    # DUTY COMMANDS - Für Support-Teammitglieder
    # ============================================

    @commands.group(name="duty", invoke_without_command=True)
    @commands.guild_only()
    async def duty(self, ctx: commands.Context):
        """
        On-Duty System für Support-Teammitglieder.
        
        Verwende `[p]duty on` um dich anzumelden
        Verwende `[p]duty off` um dich abzumelden
        Verwende `[p]duty status` um deinen Status zu sehen
        ODER benutze die Buttons in der Duty-Nachricht!
        """
        await ctx.send_help("duty")

    @duty.command(name="on", aliases=["start", "begin"])
    async def duty_on(self, ctx: commands.Context):
        """
        Melde dich für Support-Duty an.
        
        Ab jetzt wirst du bei neuen Support-Anfragen gepingt.
        """
        guild = ctx.guild
        role_id = await self.config.guild(guild).role()
        
        if not role_id:
            await ctx.send("❌ Es wurde keine Support-Rolle konfiguriert! Bitte wende dich an einen Admin.")
            return
        
        base_role = guild.get_role(role_id)
        if not base_role:
            await ctx.send("❌ Die konfigurierte Support-Rolle existiert nicht mehr!")
            return
        
        if base_role not in ctx.author.roles:
            await ctx.send(f"❌ Du benötigst die {base_role.mention} Rolle um dich auf Duty setzen zu können!")
            return
        
        # Prüfen ob bereits auf Duty
        is_on_duty = await self.config.member(ctx.author).on_duty()
        if is_on_duty:
            await ctx.send("⚠️ Du bist bereits im Duty-Modus!")
            return
        
        # Duty aktivieren und Rolle geben
        await self.config.member(ctx.author).on_duty.set(True)
        start_time = datetime.utcnow()
        await self.config.member(ctx.author).duty_start.set(start_time.timestamp())
        
        # Duty-Rolle hinzufügen
        await self.add_duty_role(ctx.author)
        
        # Auto-Duty Timer starten falls aktiviert
        auto_duty = await self.config.guild(guild).auto_remove_duty()
        duty_timeout = await self.config.guild(guild).duty_timeout()
        
        # Nachricht senden
        duty_channel_id = await self.config.guild(guild).duty_channel()
        notify_channel = ctx.channel
        if duty_channel_id:
            dc = guild.get_channel(duty_channel_id)
            if dc and isinstance(dc, discord.TextChannel):
                notify_channel = dc
        
        embed = discord.Embed(
            title="🟢 Duty Gestartet",
            description=f"{ctx.author.mention} hat sich für den Support-Dienst angemeldet!",
            color=discord.Color.green(),
            timestamp=start_time
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(name="👤 Mitarbeiter", value=f"{ctx.author.display_name}", inline=True)
        
        if auto_duty:
            end_time = start_time + timedelta(hours=duty_timeout)
            embed.add_field(name="⏰ Automatische Abmeldung", value=f"Nach {duty_timeout} Stunden\n(<t:{int(end_time.timestamp())}:R>)", inline=True)
        
        # Zähle alle aktiven Duty-User
        duty_count = 0
        for m in base_role.members:
            is_duty = await self.config.member(m).on_duty()
            if is_duty:
                duty_count += 1
        
        embed.add_field(name="📊 Aktive Supporter", value=f"🟢 {duty_count} Teammitglieder im Dienst", inline=True)
        embed.set_footer(text=f"Duty Start • {start_time.strftime('%d.%m.%Y %H:%M')}")
        
        await notify_channel.send(embed=embed)

    @duty.command(name="off", aliases=["stop", "end", "quit"])
    async def duty_off(self, ctx: commands.Context, reason: str = None):
        """
        Melde dich vom Support-Duty ab.
        
        Optionale Begründung: [p]duty off Pause / Feierabend / etc.
        """
        is_on_duty = await self.config.member(ctx.author).on_duty()
        
        if not is_on_duty:
            await ctx.send("ℹ️ Du bist aktuell nicht im Duty-Modus.")
            return
        
        # Hole Startzeit für Statistik
        start_time = await self.config.member(ctx.author).duty_start()
        duration = "Unbekannt"
        if start_time:
            start_dt = datetime.fromtimestamp(start_time)
            delta = datetime.utcnow() - start_dt
            hours = int(delta.total_seconds() // 3600)
            minutes = int((delta.total_seconds() % 3600) // 60)
            duration = f"{hours}h {minutes}min"
        
        # Duty deaktivieren und Rolle entfernen
        await self.config.member(ctx.author).on_duty.set(False)
        await self.config.member(ctx.author).duty_start.set(None)
        
        # Duty-Rolle entfernen
        await self.remove_duty_role(ctx.author)
        
        # Nachricht senden
        guild = ctx.guild
        duty_channel_id = await self.config.guild(guild).duty_channel()
        notify_channel = ctx.channel
        if duty_channel_id:
            dc = guild.get_channel(duty_channel_id)
            if dc and isinstance(dc, discord.TextChannel):
                notify_channel = dc
        
        role_id = await self.config.guild(guild).role()
        base_role = guild.get_role(role_id) if role_id else None
        
        embed = discord.Embed(
            title="🔴 Duty Beendet",
            description=f"{ctx.author.mention} hat sich vom Support-Dienst abgemeldet.",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(name="👤 Mitarbeiter", value=f"{ctx.author.display_name}", inline=True)
        embed.add_field(name="⏱️ Dauer", value=duration, inline=True)
        
        if reason:
            embed.add_field(name="📝 Grund", value=reason, inline=False)
        
        # Zähle verbleibende aktive Duty-User
        duty_count = 0
        if base_role:
            for m in base_role.members:
                is_duty = await self.config.member(m).on_duty()
                if is_duty:
                    duty_count += 1
        
        embed.add_field(name="📊 Verbleibende Supporter", value=f"🟢 {duty_count} Teammitglieder im Dienst", inline=True)
        embed.set_footer(text=f"Duty Ende • {datetime.utcnow().strftime('%d.%m.%Y %H:%M')}")
        
        await notify_channel.send(embed=embed)

    @duty.command(name="status")
    async def duty_status(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """
        Zeige den Duty-Status an.
        
        Ohne Angabe: Dein eigener Status
        Mit Member-Angabe: Status des angegebenen Users
        """
        target = member or ctx.author
        
        is_on_duty = await self.config.member(target).on_duty()
        start_time = await self.config.member(target).duty_start()
        
        if not is_on_duty:
            embed = discord.Embed(
                title="🔴 Nicht im Duty",
                description=f"{target.mention} ist aktuell **nicht** im Support-Dienst.",
                color=discord.Color.gray()
            )
            embed.set_thumbnail(url=target.display_avatar.url)
        else:
            start_dt = datetime.fromtimestamp(start_time) if start_time else datetime.utcnow()
            duration = "Unbekannt"
            if start_time:
                delta = datetime.utcnow() - start_dt
                hours = int(delta.total_seconds() // 3600)
                minutes = int((delta.total_seconds() % 3600) // 60)
                duration = f"{hours}h {minutes}min"
            
            embed = discord.Embed(
                title="🟢 Im Duty",
                description=f"{target.mention} ist aktuell im Support-Dienst!",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="⏱️ Seit", value=f"<t:{int(start_dt.timestamp())}:R>\n({duration})", inline=True)
            embed.add_field(name="🕐 Startzeit", value=f"<t:{int(start_dt.timestamp())}:f>", inline=True)
            embed.set_footer(text=f"Duty Start: {start_dt.strftime('%d.%m.%Y %H:%M:%S')}")
        
        await ctx.send(embed=embed)

    @duty.command(name="list", aliases=["all", "active"])
    @checks.mod_or_permissions(manage_guild=True)
    async def duty_list(self, ctx: commands.Context):
        """
        Zeige alle aktuellen Duty-Mitglieder an.
        
        Nur für Moderatoren und höher sichtbar.
        """
        guild = ctx.guild
        role_id = await self.config.guild(guild).role()
        
        if not role_id:
            await ctx.send("❌ Keine Support-Rolle konfiguriert!")
            return
        
        base_role = guild.get_role(role_id)
        if not base_role:
            await ctx.send("❌ Support-Rolle nicht gefunden!")
            return
        
        duty_members = []
        for m in base_role.members:
            is_on_duty = await self.config.member(m).on_duty()
            if is_on_duty:
                start_time = await self.config.member(m).duty_start()
                if start_time:
                    start_dt = datetime.fromtimestamp(start_time)
                    delta = datetime.utcnow() - start_dt
                    hours = int(delta.total_seconds() // 3600)
                    minutes = int((delta.total_seconds() % 3600) // 60)
                    duration = f"{hours}h {minutes}min"
                else:
                    duration = "Unbekannt"
                
                duty_members.append((m, duration))
        
        if not duty_members:
            embed = discord.Embed(
                title="🔴 Keine aktiven Duty-Mitglieder",
                description="Derzeit ist niemand im Support-Dienst!",
                color=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title="🟢 Aktive Duty-Mitglieder",
                description=f"**{len(duty_members)}** Teammitglieder sind im Dienst:",
                color=discord.Color.green()
            )
            
            for member, duration in duty_members[:10]:
                embed.add_field(
                    name=f"👤 {member.display_name}",
                    value=f"Seit: {duration}\nID: `{member.id}`",
                    inline=True
                )
            
            if len(duty_members) > 10:
                embed.description += f"\n...und {len(duty_members) - 10} weitere"
        
        await ctx.send(embed=embed)

    @duty.command(name="setup", aliases=["panel", "board"])
    @checks.admin_or_permissions(manage_guild=True)
    async def duty_setup(self, ctx: commands.Context):
        """
        Erstellt ein Duty-Panel mit Buttons zum An- und Abmelden.
        
        Sende eine Nachricht mit Buttons in den aktuellen Channel.
        Support-Mitarbeiter können dann einfach auf die Buttons klicken!
        """
        guild = ctx.guild
        role_id = await self.config.guild(guild).role()
        
        if not role_id:
            await ctx.send("❌ Es wurde keine Support-Rolle konfiguriert!")
            return
        
        base_role = guild.get_role(role_id)
        if not base_role:
            await ctx.send("❌ Die konfigurierte Support-Rolle existiert nicht mehr!")
            return
        
        # Erstelle das Panel Embed
        embed = discord.Embed(
            title="🎧 Support Duty Panel",
            description=(
                f"**Willkommen im Support-Duty System!**\n\n"
                f"Hier kannst du dich ganz einfach für den Support-Dienst an- und abmelden.\n"
                f"Klicke auf die Buttons unten um deinen Status zu ändern.\n\n"
                f"🟢 **On Duty**: Du wirst bei neuen Support-Anfragen gepingt\n"
                f"🔴 **Off Duty**: Du wirst nicht gepingt\n\n"
                f"Voraussetzung: Du musst die {base_role.mention} Rolle haben."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Support Duty System")
        
        # Erstelle View mit Buttons
        view = discord.ui.View(timeout=None)
        view.add_item(DutyToggleButton(is_on=True))
        view.add_item(DutyToggleButton(is_on=False))
        
        msg = await ctx.send(embed=embed, view=view)
        await ctx.send(f"✅ Duty-Panel wurde in {ctx.channel.mention} erstellt!\nNachrichten-ID: `{msg.id}`\n\n*Hinweis: Nach einem Bot-Neustart muss das Panel neu erstellt werden.*", delete_after=30)


class DutyToggleButton(discord.ui.Button):
    """Button für Duty An/Aus"""
    
    def __init__(self, is_on: bool):
        if is_on:
            super().__init__(
                style=discord.ButtonStyle.green,
                label="🟢 On Duty",
                custom_id="duty_on",
                emoji="✅"
            )
        else:
            super().__init__(
                style=discord.ButtonStyle.red,
                label="🔴 Off Duty",
                custom_id="duty_off",
                emoji="⛔"
            )
    
    async def callback(self, interaction: discord.Interaction):
        """Wird aufgerufen wenn der Button geklickt wird"""
        cog = interaction.client.get_cog("SupportCog")
        if not cog:
            await interaction.response.send_message("❌ Cog nicht gefunden!", ephemeral=True)
            return
        
        guild = interaction.guild
        member = interaction.user
        
        role_id = await cog.config.guild(guild).role()
        if not role_id:
            await interaction.response.send_message("❌ Keine Support-Rolle konfiguriert!", ephemeral=True)
            return
        
        base_role = guild.get_role(role_id)
        if not base_role:
            await interaction.response.send_message("❌ Support-Rolle nicht gefunden!", ephemeral=True)
            return
        
        if base_role not in member.roles:
            await interaction.response.send_message(
                f"❌ Du benötigst die {base_role.mention} Rolle um Duty zu nutzen!",
                ephemeral=True
            )
            return
        
        is_on_duty = await cog.config.member(member).on_duty()
        
        if self.custom_id == "duty_on":
            # On Duty setzen
            if is_on_duty:
                await interaction.response.send_message("⚠️ Du bist bereits im Duty-Modus!", ephemeral=True)
                return
            
            # Duty aktivieren
            await cog.config.member(member).on_duty.set(True)
            start_time = datetime.utcnow()
            await cog.config.member(member).duty_start.set(start_time.timestamp())
            
            # Duty-Rolle hinzufügen
            await cog.add_duty_role(member)
            
            embed = discord.Embed(
                title="🟢 Duty Gestartet",
                description=f"{member.mention} hat sich für den Support-Dienst angemeldet!",
                color=discord.Color.green(),
                timestamp=start_time
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="👤 Mitarbeiter", value=f"{member.display_name}", inline=True)
            
            # Zähle alle aktiven Duty-User
            duty_count = 0
            for m in base_role.members:
                is_duty = await cog.config.member(m).on_duty()
                if is_duty:
                    duty_count += 1
            
            embed.add_field(name="📊 Aktive Supporter", value=f"🟢 {duty_count} Teammitglieder im Dienst", inline=True)
            
            await interaction.response.send_message(embed=embed)
        
        else:  # duty_off
            # Off Duty setzen
            if not is_on_duty:
                await interaction.response.send_message("ℹ️ Du bist aktuell nicht im Duty-Modus.", ephemeral=True)
                return
            
            # Hole Startzeit für Statistik
            start_time = await cog.config.member(member).duty_start()
            duration = "Unbekannt"
            if start_time:
                start_dt = datetime.fromtimestamp(start_time)
                delta = datetime.utcnow() - start_dt
                hours = int(delta.total_seconds() // 3600)
                minutes = int((delta.total_seconds() % 3600) // 60)
                duration = f"{hours}h {minutes}min"
            
            # Duty deaktivieren
            await cog.config.member(member).on_duty.set(False)
            await cog.config.member(member).duty_start.set(None)
            
            # Duty-Rolle entfernen
            await cog.remove_duty_role(member)
            
            embed = discord.Embed(
                title="🔴 Duty Beendet",
                description=f"{member.mention} hat sich vom Support-Dienst abgemeldet.",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="👤 Mitarbeiter", value=f"{member.display_name}", inline=True)
            embed.add_field(name="⏱️ Dauer", value=duration, inline=True)
            
            # Zähle verbleibende aktive Duty-User
            duty_count = 0
            for m in base_role.members:
                is_duty = await cog.config.member(m).on_duty()
                if is_duty:
                    duty_count += 1
            
            embed.add_field(name="📊 Verbleibende Supporter", value=f"🟢 {duty_count} Teammitglieder im Dienst", inline=True)
            
            await interaction.response.send_message(embed=embed)


# ============================================
# FEEDBACK SYSTEM - Modal und Buttons
# ============================================

class FeedbackModal(discord.ui.Modal, title="Feedback senden"):
    """Modal für Feedback-Eingabe"""
    
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.feedback_text = discord.ui.TextInput(
            label="Dein Feedback",
            style=discord.TextStyle.paragraph,
            placeholder="Beschreibe deine Erfahrung mit dem Support...",
            min_length=10,
            max_length=2000,
            required=True
        )
        self.append_item(self.feedback_text)
    
    async def callback(self, interaction: discord.Interaction):
        """Wird aufgerufen wenn das Modal abgeschickt wird"""
        guild = interaction.guild
        feedback_channel_id = await self.cog.config.guild(guild).feedback_channel()
        
        if not feedback_channel_id:
            await interaction.response.send_message(
                "❌ Das Feedback-System ist derzeit nicht konfiguriert!",
                ephemeral=True
            )
            return
        
        feedback_channel = guild.get_channel(feedback_channel_id)
        if not feedback_channel:
            await interaction.response.send_message(
                "❌ Der Feedback-Channel wurde nicht gefunden!",
                ephemeral=True
            )
            return
        
        feedback_content = self.feedback_text.value
        
        # Erstelle Embed für Feedback
        embed = discord.Embed(
            title="📝 Neues Feedback erhalten",
            description=feedback_content,
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="👤 Nutzer", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
        embed.add_field(name="📍 Channel", value=f"{interaction.channel.mention}", inline=True)
        embed.set_footer(text=f"Feedback von {interaction.user.display_name}")
        
        # Sende ins Feedback-Channel
        await feedback_channel.send(embed=embed)
        
        # Bestätigung an User
        confirm_embed = discord.Embed(
            title="✅ Feedback gesendet",
            description="Vielen Dank für dein Feedback! Wir werden es sorgfältig prüfen.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        await interaction.response.send_message(embed=confirm_embed, ephemeral=True)


class FeedbackButton(discord.ui.Button):
    """Button zum Öffnen des Feedback-Modals"""
    
    def __init__(self, cog):
        super().__init__(
            style=discord.ButtonStyle.blurple,
            label="Feedback geben",
            custom_id="feedback_button",
            emoji="📝"
        )
        self.cog = cog
    
    async def callback(self, interaction: discord.Interaction):
        """Wird aufgerufen wenn der Button geklickt wird"""
        modal = FeedbackModal(self.cog)
        await interaction.response.send_modal(modal)


# ============================================
# SUPPORT CALL / ALERT SYSTEM
# ============================================

class SupportCallView(discord.ui.View):
    """View für Support-Alarm Buttons"""
    
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
    
    @discord.ui.button(
        label="🔴 Support rufen",
        style=discord.ButtonStyle.red,
        custom_id="support_call_button",
        emoji="📢"
    )
    async def support_call(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Support-Alarm auslösen"""
        guild = interaction.guild
        member = interaction.user
        
        # Hole Konfiguration
        call_channel_id = await self.cog.config.guild(guild).supportcall_channel()
        default_channel_id = await self.cog.config.guild(guild).channel()
        call_role_id = await self.cog.config.guild(guild).supportcall_role()
        
        # Bestimme den Channel (supportcall_channel oder fallback zu channel)
        target_channel_id = call_channel_id if call_channel_id else default_channel_id
        
        if not target_channel_id:
            await interaction.response.send_message(
                "❌ Kein Support-Channel konfiguriert!",
                ephemeral=True
            )
            return
        
        target_channel = guild.get_channel(target_channel_id)
        if not target_channel:
            await interaction.response.send_message(
                "❌ Der Ziel-Channel wurde nicht gefunden!",
                ephemeral=True
            )
            return
        
        # Bestimme die zu pingende Rolle
        ping_role = None
        if call_role_id:
            ping_role = guild.get_role(call_role_id)
        
        if not ping_role:
            # Fallback: Duty-Rolle oder Basis-Supportrolle
            duty_role = await self.cog.get_or_create_duty_role(guild)
            if duty_role:
                ping_role = duty_role
            else:
                role_id = await self.cog.config.guild(guild).role()
                if role_id:
                    ping_role = guild.get_role(role_id)
        
        if not ping_role:
            await interaction.response.send_message(
                "❌ Keine Rolle für Supportrufe konfiguriert!",
                ephemeral=True
            )
            return
        
        # Erstelle Alarm-Embed
        embed = discord.Embed(
            title="🚨 SUPPORT-ALARM!",
            description=(
                f"**{member.mention}** benötigt sofortige Unterstützung!\n\n"
                f"Bitte findet euch umgehend im entsprechenden Channel ein."
            ),
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Rufer", value=f"{member.display_name}\n(`{member.id}`)", inline=True)
        embed.add_field(name="📍 Aktueller Channel", value=f"{interaction.channel.mention}", inline=True)
        embed.add_field(name="🎯 Ziel-Channel", value=f"{target_channel.mention}", inline=True)
        embed.add_field(
            name="⏰ Zeitpunkt",
            value=f"<t:{int(datetime.utcnow().timestamp())}:f>\n(<t:{int(datetime.utcnow().timestamp())}:R>)",
            inline=True
        )
        embed.set_footer(text="🔴 Dringender Support-Alarm")
        
        # Sende Alarm im Ziel-Channel mit Role-Ping
        await target_channel.send(content=f"{ping_role.mention}", embed=embed)
        
        # Bestätigung an den Rufer
        confirm_embed = discord.Embed(
            title="✅ Support-Alarm ausgelöst",
            description=(
                f"Der Alarm wurde erfolgreich gesendet!\n\n"
                f"Die Support-Mitarbeiter wurden in {target_channel.mention} benachrichtigt.\n"
                f"Bitte warte dort auf Hilfe."
            ),
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        await interaction.response.send_message(embed=confirm_embed, ephemeral=True)


async def setup(bot: Red):
    """Lädt den Cog"""
    cog = SupportCog(bot)
    await bot.add_cog(cog)
    
    # Persistente Views registrieren
    bot.add_view(DutyToggleButton(is_on=True))
    bot.add_view(DutyToggleButton(is_on=False))
    bot.add_view(FeedbackButton(cog))
    bot.add_view(SupportCallView(cog))


async def teardown(bot: Red):
    """Entfernt den Cog"""
    await bot.remove_cog("SupportCog")
