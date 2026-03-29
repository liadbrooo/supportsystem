"""
Support Warteraum Cog für RedBot mit On-Duty System

Dieser Cog erkennt, wenn ein Nutzer einen Support-Warteraum betritt oder verlässt
und sendet eine Nachricht in einem konfigurierten Text-Channel.
Enthält ein On-Duty System für Support-Teammitglieder.

Installation:
1. Kopiere den gesamten 'supportcog' Ordner in deinen RedBot cogs Ordner
   (normalerweise ~/.local/share/Red-DiscordBot/data/[DEIN_BOT_NAME]/cogs/)
2. Lade den Cog mit: [p]load supportcog
3. Konfiguriere mit:
   - [p]supportset channel #textchannel  (Setzt den Text-Channel für Benachrichtigungen)
   - [p]supportset room @VoiceChannel    (Setzt den Voice-Warteraum)
   - [p]supportset role @Rolle           (Setzt die Rolle, die gepingt wird)
   - ODER verwende [p]supportset setup für einen interaktiven Einrichtungsassistenten

Nutzung:
- Wenn jemand den konfigurierten Voice-Channel betritt, wird automatisch
  eine schöne Nachricht im Text-Channel gesendet.
- Support-Teamler können sich mit [p]duty on/off an- und abmelden
- Nur angemeldete Teamler werden gepingt!
"""

import discord
from redbot.core import commands, checks, Config
from redbot.core.bot import Red
from datetime import datetime, timedelta
from typing import Optional
import asyncio


class SupportCog(commands.Cog):
    """Cog für Support-Warteraum Benachrichtigungen"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=12345678901234567890)
        
        default_guild_settings = {
            "channel": None,  # Text-Channel ID für Benachrichtigungen
            "room": None,     # Voice-Channel ID des Warteraums
            "role": None,     # Rolle ID die gepingt wird (Basis-Supportrolle)
            "use_embed": True,  # Ob Embeds verwendet werden sollen
            "enabled": True,   # Ob der Cog aktiv ist
            "duty_channel": None,  # Channel für Duty-Nachrichten
            "auto_remove_duty": True,  # Automatisch Duty entfernen nach X Stunden
            "duty_timeout": 4  # Stunden nach denen Duty automatisch entfernt wird
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
            
            # Bestimme den Channel für die Nachricht
            notify_channel = channel
            if duty_channel_id:
                duty_ch = guild.get_channel(duty_channel_id)
                if duty_ch and isinstance(duty_ch, discord.TextChannel):
                    notify_channel = duty_ch
            
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
    async def supportset_autoduty(self, ctx: commands.Context, hours: int = None):
        """
        Konfiguriere automatisches Duty-Ende nach X Stunden.
        
        - `0` oder `off`: Automatisches Beenden deaktivieren
        - `1-24`: Anzahl der Stunden nach denen Duty automatisch endet
        """
        if hours is None or hours <= 0:
            await self.config.guild(ctx.guild).auto_remove_duty.set(False)
            await ctx.send("✅ Automatisches Duty-Ende deaktiviert.")
        else:
            if hours > 24:
                hours = 24
            await self.config.guild(ctx.guild).auto_remove_duty.set(True)
            await self.config.guild(ctx.guild).duty_timeout.set(hours)
            await ctx.send(f"✅ Duty wird automatisch nach {hours} Stunden beendet.")

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
        
        # Duty aktivieren
        await self.config.member(ctx.author).on_duty.set(True)
        start_time = datetime.utcnow()
        await self.config.member(ctx.author).duty_start.set(start_time.timestamp())
        
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
        
        # Duty deaktivieren
        await self.config.member(ctx.author).on_duty.set(False)
        await self.config.member(ctx.author).duty_start.set(None)
        
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


async def setup(bot: Red):
    """Lädt den Cog"""
    await bot.add_cog(SupportCog(bot))


async def teardown(bot: Red):
    """Entfernt den Cog"""
    await bot.remove_cog("SupportCog")
