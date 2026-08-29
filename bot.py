import asyncio
import json
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DATA_FILE = Path("driver_market.json")
data_lock = asyncio.Lock()

# =========================================================
# TEAM-ROLLEN
# =========================================================
# Hier können später weitere Team-Rollen ergänzt werden.
# Die Zahl ist jeweils die Discord-Rollen-ID.

TEAM_ROLES = {
    "Red Bull Racing": 1542621152831209563,
    "Ferrari": 1542621040058826842,
}

TRANSFER_TEAM_CHOICES = [
    app_commands.Choice(name=team_name, value=team_name)
    for team_name in TEAM_ROLES
]
TRANSFER_TEAM_CHOICES.append(
    app_commands.Choice(name="Frei / Free Agent", value="Frei")
)


def get_team_role_mention(team_name: str) -> str | None:
    """Gibt die Discord-Rollen-Erwähnung für ein Team zurück."""
    role_id = TEAM_ROLES.get(team_name)
    if role_id:
        return f"<@&{role_id}>"
    return None


def get_team_from_member(member: discord.Member) -> str:
    """
    Erkennt das Team automatisch anhand der Discord-Rollen des Fahrers.
    Wenn keine bekannte Teamrolle gefunden wird, gilt der Fahrer als frei.
    """
    member_role_ids = {role.id for role in member.roles}

    for team_name, role_id in TEAM_ROLES.items():
        if role_id in member_role_ids:
            return team_name

    return "Frei"


async def change_member_team_role(
    member: discord.Member,
    new_team: str,
) -> tuple[bool, str]:
    """
    Entfernt bekannte Teamrollen und weist bei Bedarf die neue Teamrolle zu.
    Bei 'Frei' werden nur die bekannten Teamrollen entfernt.
    """
    known_role_ids = set(TEAM_ROLES.values())
    roles_to_remove = [
        role for role in member.roles
        if role.id in known_role_ids
    ]

    try:
        if roles_to_remove:
            await member.remove_roles(
                *roles_to_remove,
                reason="Driver Market Transfer",
            )

        if new_team != "Frei":
            role_id = TEAM_ROLES.get(new_team)
            role = member.guild.get_role(role_id) if role_id else None

            if role is None:
                return False, (
                    f"Die Discord-Rolle für **{new_team}** "
                    "wurde auf dem Server nicht gefunden."
                )

            await member.add_roles(
                role,
                reason="Driver Market Transfer",
            )

    except discord.Forbidden:
        return False, (
            "Ich darf die Teamrolle nicht ändern. "
            "Gib dem Bot die Berechtigung **Rollen verwalten** "
            "und stelle seine Bot-Rolle über die Teamrollen."
        )
    except discord.HTTPException as exc:
        return False, f"Discord konnte die Rollen nicht ändern: {exc}"

    return True, ""


def load_data() -> dict:
    if not DATA_FILE.exists():
        return {"guilds": {}}

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"guilds": {}}


def save_data(data: dict) -> None:
    temp_file = DATA_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_file.replace(DATA_FILE)


def get_guild_drivers(data: dict, guild_id: int) -> list[dict]:
    guilds = data.setdefault("guilds", {})
    return guilds.setdefault(str(guild_id), [])


def get_guild_season(data: dict, guild_id: int) -> int:
    seasons = data.setdefault("seasons", {})
    return int(seasons.get(str(guild_id), 1))


def set_guild_season(data: dict, guild_id: int, season: int) -> None:
    seasons = data.setdefault("seasons", {})
    seasons[str(guild_id)] = int(season)


def format_driver_line(driver: dict) -> str:
    return (
        f"**{driver['name']}** — {driver['team']} / "
        f"{driver['contract']} / {driver['market_value']}"
    )


class TransferBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"{len(synced)} Slash-Commands mit Server {GUILD_ID} synchronisiert.")
        else:
            synced = await self.tree.sync()
            print(f"{len(synced)} globale Slash-Commands synchronisiert.")


bot = TransferBot()


class MarketView(discord.ui.View):
    def __init__(
        self,
        teams: list[tuple[str, list[dict]]],
        author_id: int,
        season: int,
    ):
        super().__init__(timeout=300)
        self.teams = teams
        self.author_id = author_id
        self.season = season
        self.index = 0
        self.message: discord.Message | None = None
        self.update_buttons()

    def update_buttons(self) -> None:
        self.previous.disabled = self.index <= 0
        self.next.disabled = self.index >= len(self.teams) - 1

    def make_embed(self) -> discord.Embed:
        team_name, drivers = self.teams[self.index]

        team_role = get_team_role_mention(team_name)

        description = (
            f"**Saison {self.season}**\n"
            f"Team {self.index + 1} von {len(self.teams)}"
        )
        if team_role:
            description += f"\n**Team:** {team_role}"

        embed = discord.Embed(
            title=f"🏎️ Fahrermarkt — {team_name}",
            description=description,
        )

        for number, driver in enumerate(drivers, start=1):
            embed.add_field(
                name=f"{number}. {driver['name']}",
                value=(
                    f"**Discord:** <@{driver['user_id']}>\n"
                    f"**Marktwert:** {driver['market_value']}\n"
                    f"**Vertrag:** {driver['contract']}\n"
                    f"**Ausstiegsklausel:** {driver['release_clause']}\n"
                    f"**Gehalt:** {driver['salary']}\n"
                    f"**Boni:** {driver['bonuses']}"
                ),
                inline=False,
            )

        embed.set_footer(text="◀ ▶ zum Wechseln des Teams")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Öffne bitte deinen eigenen Fahrermarkt mit `/drivermarket`.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.index -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.index += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class AllDriversView(discord.ui.View):
    def __init__(
        self,
        drivers: list[dict],
        author_id: int,
        season: int,
        per_page: int = 15,
    ):
        super().__init__(timeout=300)
        self.drivers = drivers
        self.author_id = author_id
        self.season = season
        self.per_page = per_page
        self.index = 0
        self.message: discord.Message | None = None
        self.page_count = max(1, (len(drivers) + per_page - 1) // per_page)
        self.update_buttons()

    def update_buttons(self) -> None:
        self.previous.disabled = self.index <= 0
        self.next.disabled = self.index >= self.page_count - 1

    def make_embed(self) -> discord.Embed:
        start = self.index * self.per_page
        end = start + self.per_page
        page_drivers = self.drivers[start:end]

        lines = [format_driver_line(driver) for driver in page_drivers]
        embed = discord.Embed(
            title=f"👥 Alle Fahrer • Saison {self.season}",
            description="\n".join(lines),
        )
        embed.set_footer(
            text=f"Seite {self.index + 1}/{self.page_count} • Name — Team / Vertrag / Marktwert"
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Öffne bitte deine eigene Fahrerliste mit `/alldrivers`.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.index -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.index += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


@bot.tree.command(name="drivermarket", description="Zeigt den aktuellen Fahrermarkt nach Teams an.")
@app_commands.guild_only()
async def drivermarket(interaction: discord.Interaction) -> None:
    data = load_data()
    drivers = get_guild_drivers(data, interaction.guild_id)
    season = get_guild_season(data, interaction.guild_id)

    if not drivers:
        await interaction.response.send_message(
            "Der Fahrermarkt ist noch leer. Füge zuerst einen Fahrer mit `/adddriver` hinzu."
        )
        return

    grouped: dict[str, list[dict]] = {}
    for driver in drivers:
        grouped.setdefault(driver["team"], []).append(driver)

    def team_sort(item: tuple[str, list[dict]]) -> tuple[int, str]:
        team_name = item[0].strip().lower()
        is_free = team_name in {"frei", "free", "free agent", "free agents"}
        return (1 if is_free else 0, team_name)

    teams = sorted(grouped.items(), key=team_sort)
    view = MarketView(teams, interaction.user.id, season)

    await interaction.response.send_message(embed=view.make_embed(), view=view)
    view.message = await interaction.original_response()


@bot.tree.command(name="alldrivers", description="Zeigt alle Fahrer kompakt in einer Liste an.")
@app_commands.guild_only()
async def alldrivers(interaction: discord.Interaction) -> None:
    data = load_data()
    drivers = get_guild_drivers(data, interaction.guild_id)
    season = get_guild_season(data, interaction.guild_id)

    if not drivers:
        await interaction.response.send_message(
            "Die Fahrerliste ist noch leer. Füge zuerst einen Fahrer mit `/adddriver` hinzu."
        )
        return

    sorted_drivers = sorted(
        drivers,
        key=lambda d: (
            1 if d["team"].strip().lower() in {"frei", "free", "free agent", "free agents"} else 0,
            d["team"].lower(),
            d["name"].lower(),
        ),
    )

    view = AllDriversView(sorted_drivers, interaction.user.id, season)
    await interaction.response.send_message(embed=view.make_embed(), view=view)
    view.message = await interaction.original_response()


@bot.tree.command(name="driverinfo", description="Zeigt alle Vertragsinformationen eines Fahrers an.")
@app_commands.describe(fahrer="Discord-Mitglied, dessen Informationen angezeigt werden sollen")
@app_commands.guild_only()
async def driverinfo(interaction: discord.Interaction, fahrer: discord.Member) -> None:
    data = load_data()
    drivers = get_guild_drivers(data, interaction.guild_id)
    season = get_guild_season(data, interaction.guild_id)
    driver = next((d for d in drivers if d["user_id"] == fahrer.id), None)

    if not driver:
        await interaction.response.send_message(
            f"❌ **{fahrer.display_name}** ist nicht im Fahrermarkt eingetragen.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"📄 Fahrerinfo — {driver['name']}",
        description=f"<@{driver['user_id']}>",
    )
    team_role = get_team_role_mention(driver["team"])
    team_value = team_role if team_role else driver["team"]

    embed.add_field(name="Team", value=team_value, inline=True)
    embed.add_field(name="Marktwert", value=driver["market_value"], inline=True)
    embed.add_field(name="Vertrag", value=driver["contract"], inline=False)
    embed.add_field(name="Ausstiegsklausel", value=driver["release_clause"], inline=True)
    embed.add_field(name="Gehalt", value=driver["salary"], inline=True)
    embed.add_field(name="Boni", value=driver["bonuses"], inline=False)
    embed.set_footer(text=f"WTV Driver Market • Saison {season}")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="help",
    description="Zeigt alle verfügbaren Commands und deren Berechtigungen.",
)
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction) -> None:
    data = load_data()
    season = get_guild_season(data, interaction.guild_id)

    is_admin = (
        isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    )

    embed = discord.Embed(
        title="📖 WTV DRIVER MARKET • HELP",
        description=(
            f"**Aktuelle Saison: {season}**\n\n"
            "Hier findest du alle Commands der V2.1.\n"
            f"**Dein Zugriff:** {'✅ Administrator' if is_admin else '🌍 Öffentliche Commands'}"
        ),
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="🌍 Öffentliche Commands",
        value=(
            "`/drivermarket` – Fahrermarkt nach Teams\n"
            "`/alldrivers` – alle Fahrer kompakt\n"
            "`/driverinfo` – Informationen zu einem Fahrer\n"
            "`/help` – diese Übersicht"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛡️ Nur Administrator",
        value=(
            "`/adddriver` – Fahrer hinzufügen/aktualisieren\n"
            "`/editdriver` – Fahrerdaten ändern\n"
            "`/transferdriver` – Fahrer transferieren\n"
            "`/removedriver` – Fahrer entfernen\n"
            "`/saison` – aktuelle Saisonnummer direkt setzen"
        ),
        inline=False,
    )

    embed.set_footer(text=f"WTV Driver Market V2.2 • Saison {season}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="saison",
    description="Setzt die aktuelle Saisonnummer direkt.",
)
@app_commands.describe(nummer="Saisonnummer, z. B. 6")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def saison(
    interaction: discord.Interaction,
    nummer: app_commands.Range[int, 1, 999],
) -> None:
    async with data_lock:
        data = load_data()
        old_season = get_guild_season(data, interaction.guild_id)
        set_guild_season(data, interaction.guild_id, nummer)
        save_data(data)

    embed = discord.Embed(
        title="🏁 Saison gesetzt",
        description=(
            f"**Saison {old_season} → Saison {nummer}**\n\n"
            "Die Saisonnummer wurde geändert. "
            "Fahrer, Verträge, Marktwerte und andere Daten wurden nicht verändert."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"WTV Driver Market V2.2 • Saison {nummer}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="adddriver", description="Fügt einen Fahrer hinzu oder aktualisiert ihn.")
@app_commands.describe(
    fahrer="Discord-Mitglied",
    marktwert="z. B. 25 Mio. €",
    vertrag="z. B. bis Saisonende 2027 oder Kein Vertrag",
    ausstiegsklausel="z. B. 40 Mio. € oder Keine",
    gehalt="z. B. 8 Mio. €/Saison oder -",
    boni="z. B. 1 Mio. € pro Sieg, Keine oder -",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def adddriver(
    interaction: discord.Interaction,
    fahrer: discord.Member,
    marktwert: str,
    vertrag: str,
    ausstiegsklausel: str,
    gehalt: str,
    boni: str,
) -> None:
    # Team automatisch anhand der Rolle des ausgewählten Fahrers erkennen.
    # Hat der Fahrer keine bekannte Teamrolle, wird er automatisch als "Frei" eingetragen.
    team = get_team_from_member(fahrer)

    new_driver = {
        "user_id": fahrer.id,
        "name": fahrer.display_name,
        "team": team,
        "market_value": marktwert.strip(),
        "contract": vertrag.strip(),
        "release_clause": ausstiegsklausel.strip(),
        "salary": gehalt.strip(),
        "bonuses": boni.strip(),
    }

    async with data_lock:
        data = load_data()
        drivers = get_guild_drivers(data, interaction.guild_id)

        existing = next((d for d in drivers if d["user_id"] == fahrer.id), None)
        if existing:
            existing.update(new_driver)
            action = "aktualisiert"
        else:
            drivers.append(new_driver)
            action = "hinzugefügt"

        save_data(data)

    team_role = get_team_role_mention(team)

    await interaction.response.send_message(
        f"✅ **{fahrer.display_name}** wurde {action}.\n"
        f"**Status:** {team_role or 'Freier Fahrer'}",
        ephemeral=True,
    )


@bot.tree.command(
    name="editdriver",
    description="Ändert einzelne Daten eines bereits eingetragenen Fahrers.",
)
@app_commands.describe(
    fahrer="Fahrer, dessen Daten geändert werden sollen",
    marktwert="Neuer Marktwert; leer lassen = unverändert",
    vertrag="Neuer Vertrag; leer lassen = unverändert",
    ausstiegsklausel="Neue Ausstiegsklausel; leer lassen = unverändert",
    gehalt="Neues Gehalt; leer lassen = unverändert",
    boni="Neue Boni; leer lassen = unverändert",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def editdriver(
    interaction: discord.Interaction,
    fahrer: discord.Member,
    marktwert: str | None = None,
    vertrag: str | None = None,
    ausstiegsklausel: str | None = None,
    gehalt: str | None = None,
    boni: str | None = None,
) -> None:
    changes = []

    async with data_lock:
        data = load_data()
        drivers = get_guild_drivers(data, interaction.guild_id)
        driver = next((d for d in drivers if d["user_id"] == fahrer.id), None)

        if not driver:
            await interaction.response.send_message(
                f"❌ **{fahrer.display_name}** ist nicht im Fahrermarkt eingetragen.",
                ephemeral=True,
            )
            return

        # Anzeigenamen bei jeder Bearbeitung aktuell halten
        driver["name"] = fahrer.display_name

        if marktwert is not None and marktwert.strip():
            driver["market_value"] = marktwert.strip()
            changes.append(f"**Marktwert:** {driver['market_value']}")

        if vertrag is not None and vertrag.strip():
            driver["contract"] = vertrag.strip()
            changes.append(f"**Vertrag:** {driver['contract']}")

        if ausstiegsklausel is not None and ausstiegsklausel.strip():
            driver["release_clause"] = ausstiegsklausel.strip()
            changes.append(f"**Ausstiegsklausel:** {driver['release_clause']}")

        if gehalt is not None and gehalt.strip():
            driver["salary"] = gehalt.strip()
            changes.append(f"**Gehalt:** {driver['salary']}")

        if boni is not None and boni.strip():
            driver["bonuses"] = boni.strip()
            changes.append(f"**Boni:** {driver['bonuses']}")

        if not changes:
            await interaction.response.send_message(
                "❌ Du hast keine neuen Werte angegeben.",
                ephemeral=True,
            )
            return

        save_data(data)

    await interaction.response.send_message(
        f"✏️ **{fahrer.display_name}** wurde aktualisiert.\n\n"
        + "\n".join(changes),
        ephemeral=True,
    )


@bot.tree.command(
    name="transferdriver",
    description="Transferiert einen Fahrer zu einem anderen Team oder macht ihn zum Free Agent.",
)
@app_commands.describe(
    fahrer="Fahrer, der transferiert werden soll",
    neues_team="Neues Team oder Frei / Free Agent",
    marktwert="Optional: neuer Marktwert",
    vertrag="Optional: neuer Vertrag",
    ausstiegsklausel="Optional: neue Ausstiegsklausel",
    gehalt="Optional: neues Gehalt",
    boni="Optional: neue Boni",
)
@app_commands.choices(neues_team=TRANSFER_TEAM_CHOICES)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def transferdriver(
    interaction: discord.Interaction,
    fahrer: discord.Member,
    neues_team: app_commands.Choice[str],
    marktwert: str | None = None,
    vertrag: str | None = None,
    ausstiegsklausel: str | None = None,
    gehalt: str | None = None,
    boni: str | None = None,
) -> None:
    data = load_data()
    drivers = get_guild_drivers(data, interaction.guild_id)
    driver = next((d for d in drivers if d["user_id"] == fahrer.id), None)

    if not driver:
        await interaction.response.send_message(
            f"❌ **{fahrer.display_name}** ist nicht im Fahrermarkt eingetragen.",
            ephemeral=True,
        )
        return

    old_team = driver["team"]
    target_team = neues_team.value

    # Zuerst Discord-Rolle ändern, damit Datenbank und Server nicht auseinanderlaufen.
    role_ok, role_error = await change_member_team_role(fahrer, target_team)

    if not role_ok:
        await interaction.response.send_message(
            f"❌ Transfer wurde **nicht durchgeführt**.\n{role_error}",
            ephemeral=True,
        )
        return

    async with data_lock:
        # Noch einmal frisch laden, falls sich zwischenzeitlich etwas geändert hat.
        data = load_data()
        drivers = get_guild_drivers(data, interaction.guild_id)
        driver = next((d for d in drivers if d["user_id"] == fahrer.id), None)

        if not driver:
            await interaction.response.send_message(
                f"❌ **{fahrer.display_name}** ist nicht mehr im Fahrermarkt eingetragen.",
                ephemeral=True,
            )
            return

        driver["name"] = fahrer.display_name
        driver["team"] = target_team

        changed_contract = []

        if marktwert is not None and marktwert.strip():
            driver["market_value"] = marktwert.strip()
            changed_contract.append(f"**Marktwert:** {driver['market_value']}")

        if vertrag is not None and vertrag.strip():
            driver["contract"] = vertrag.strip()
            changed_contract.append(f"**Vertrag:** {driver['contract']}")

        if ausstiegsklausel is not None and ausstiegsklausel.strip():
            driver["release_clause"] = ausstiegsklausel.strip()
            changed_contract.append(
                f"**Ausstiegsklausel:** {driver['release_clause']}"
            )

        if gehalt is not None and gehalt.strip():
            driver["salary"] = gehalt.strip()
            changed_contract.append(f"**Gehalt:** {driver['salary']}")

        if boni is not None and boni.strip():
            driver["bonuses"] = boni.strip()
            changed_contract.append(f"**Boni:** {driver['bonuses']}")

        save_data(data)

    old_team_display = get_team_role_mention(old_team) or (
        "Freier Fahrer" if old_team == "Frei" else old_team
    )
    new_team_display = get_team_role_mention(target_team) or "Freier Fahrer"

    details = ""
    if changed_contract:
        details = "\n\n" + "\n".join(changed_contract)

    await interaction.response.send_message(
        f"🔄 **Transfer durchgeführt**\n\n"
        f"**Fahrer:** {fahrer.mention}\n"
        f"**Von:** {old_team_display}\n"
        f"**Zu:** {new_team_display}"
        f"{details}",
        ephemeral=True,
    )


@bot.tree.command(name="removedriver", description="Entfernt einen Fahrer komplett aus dem Fahrermarkt.")
@app_commands.describe(fahrer="Discord-Mitglied, das komplett entfernt werden soll")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def removedriver(
    interaction: discord.Interaction, fahrer: discord.Member
) -> None:
    async with data_lock:
        data = load_data()
        drivers = get_guild_drivers(data, interaction.guild_id)
        before = len(drivers)
        drivers[:] = [d for d in drivers if d["user_id"] != fahrer.id]

        if len(drivers) == before:
            await interaction.response.send_message(
                f"❌ **{fahrer.display_name}** ist nicht im Fahrermarkt.",
                ephemeral=True,
            )
            return

        save_data(data)

    await interaction.response.send_message(
        f"🗑️ **{fahrer.display_name}** wurde komplett aus dem Fahrermarkt entfernt.",
        ephemeral=True,
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Dafür brauchst du die Discord-Berechtigung **Administrator**."
    else:
        print(f"Command-Fehler: {error}")
        message = "❌ Beim Ausführen des Commands ist ein Fehler aufgetreten."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.event
async def on_ready() -> None:
    print(f"Bot ist online als {bot.user}.")


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env-Datei.")

bot.run(TOKEN)
