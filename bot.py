import asyncio
import json
import os
import re
import time
import shutil
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
VOLUME_MOUNT = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
DATA_FILE = (Path(VOLUME_MOUNT) / "driver_market.json") if VOLUME_MOUNT else Path("driver_market.json")
BACKUP_FILE = DATA_FILE.with_name("driver_market.backup.json")
data_lock = asyncio.Lock()

BOT_VERSION = "V2.3"
# WTV-Serverfarben aus dem aktuellen Logo: Rot / Grün / Weiß.
WTV_COLOR = discord.Color(0xD71920)
WTV_GREEN = discord.Color(0x169B62)
WTV_WHITE = discord.Color(0xF2F2F2)
SUCCESS_COLOR = WTV_GREEN
WARNING_COLOR = discord.Color(0xF2B84B)
DANGER_COLOR = discord.Color(0xA30F16)

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


def _empty_data() -> dict:
    return {
        "schema_version": 2,
        "guilds": {},
        "seasons": {},
        "transfers": {},
        "audit_logs": {},
    }


def _prepare_data(data: dict) -> dict:
    data.setdefault("schema_version", 2)
    data.setdefault("guilds", {})
    data.setdefault("seasons", {})
    data.setdefault("transfers", {})
    data.setdefault("audit_logs", {})
    return data


def load_data() -> dict:
    """
    Lädt bevorzugt die Hauptdatei. Falls diese beschädigt oder nicht vorhanden
    ist, wird automatisch das letzte Backup versucht.
    """
    for candidate in (DATA_FILE, BACKUP_FILE):
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as f:
                return _prepare_data(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue

    return _empty_data()


def save_data(data: dict) -> None:
    """
    Speichert atomar. Vor dem Überschreiben wird die letzte vorhandene
    Hauptdatei zusätzlich als driver_market.backup.json gesichert.
    """
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    if DATA_FILE.exists():
        try:
            shutil.copy2(DATA_FILE, BACKUP_FILE)
        except OSError:
            pass

    temp_file = DATA_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(_prepare_data(data), f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    temp_file.replace(DATA_FILE)


def get_guild_drivers(data: dict, guild_id: int) -> list[dict]:
    guilds = data.setdefault("guilds", {})
    drivers = guilds.setdefault(str(guild_id), [])
    for driver in drivers:
        driver.setdefault("release_clause", "-")
        driver.setdefault("driver_exit_clause", "-")
        driver.setdefault("team_termination_clause", "-")
        driver.setdefault("bonuses", "-")
        driver.setdefault("salary", "-")
    return drivers


def get_guild_season(data: dict, guild_id: int) -> int:
    seasons = data.setdefault("seasons", {})
    return int(seasons.get(str(guild_id), 1))


def set_guild_season(data: dict, guild_id: int, season: int) -> None:
    seasons = data.setdefault("seasons", {})
    seasons[str(guild_id)] = int(season)


def get_guild_transfers(data: dict, guild_id: int) -> list[dict]:
    transfers = data.setdefault("transfers", {})
    return transfers.setdefault(str(guild_id), [])


def get_guild_audit(data: dict, guild_id: int) -> list[dict]:
    logs = data.setdefault("audit_logs", {})
    return logs.setdefault(str(guild_id), [])


def add_audit(
    data: dict,
    guild_id: int,
    actor_id: int,
    action: str,
    details: str,
    *,
    season: int | None = None,
) -> None:
    if season is None:
        season = get_guild_season(data, guild_id)
    log = get_guild_audit(data, guild_id)
    log.append({
        "timestamp": int(time.time()),
        "season": int(season),
        "actor_id": int(actor_id),
        "action": action,
        "details": details,
    })
    # Schutz vor unbegrenzt wachsender Datei.
    if len(log) > 2000:
        del log[:-2000]


def storage_is_persistent() -> bool:
    if not VOLUME_MOUNT:
        return False
    try:
        return DATA_FILE.resolve().is_relative_to(Path(VOLUME_MOUNT).resolve())
    except (OSError, AttributeError):
        return str(DATA_FILE).startswith(str(Path(VOLUME_MOUNT)))


def _number_text(value: float) -> str:
    if abs(value - round(value)) < 0.000001:
        return str(int(round(value)))
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def normalize_money(raw: str, *, salary: bool = False) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("Bitte gib eine Zahl ein.")

    lowered = text.lower().strip()
    if lowered in {"-", "keine", "kein", "none"}:
        return "-"

    cleaned = lowered
    for token in ["millionen", "million", "mio.", "mio", "eur", "€", "/saison", "pro saison"]:
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.strip().replace(" ", "")

    # Deutsche Dezimalzahlen unterstützen, z. B. 12,5.
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")

    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise ValueError("Bitte nur eine Zahl eingeben, z. B. `25` oder `12,5`.")

    number = float(cleaned)
    if number < 0:
        raise ValueError("Die Zahl darf nicht negativ sein.")

    # Große Rohwerte werden als Euro interpretiert, kleine Werte als Mio. €.
    if number >= 10_000:
        number /= 1_000_000

    result = f"{_number_text(number)} Mio. €"
    if salary:
        result += "/Saison"
    return result


def normalize_contract(raw: str) -> str:
    text = raw.strip().lower()
    match = re.search(r"\d+", text)
    if not match:
        raise ValueError("Bitte gib die Vertragslaufzeit als Zahl an, z. B. `3`.")
    seasons = int(match.group())
    if seasons < 0 or seasons > 99:
        raise ValueError("Die Vertragslaufzeit muss zwischen 0 und 99 Saisons liegen.")
    if seasons == 0:
        return "Kein Vertrag"
    if seasons == 1:
        return "1 Saison"
    return f"{seasons} Saisons"


def find_driver(drivers: list[dict], user_id: int) -> dict | None:
    return next((d for d in drivers if int(d.get("user_id", 0)) == int(user_id)), None)


def display_team(team: str) -> str:
    return get_team_role_mention(team) or ("Freier Fahrer" if team == "Frei" else team)


def parse_user_id(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def resolve_member(interaction: discord.Interaction, value: str) -> discord.Member | None:
    if interaction.guild is None:
        return None
    user_id = parse_user_id(value)
    if user_id is None:
        return None
    member = interaction.guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await interaction.guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def guild_member_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []
    query = current.casefold().strip()
    result: list[app_commands.Choice[str]] = []
    for member in interaction.guild.members:
        if member.bot:
            continue
        haystack = f"{member.display_name} {member.name}".casefold()
        if query and query not in haystack:
            continue
        label = member.display_name
        if member.name.casefold() != member.display_name.casefold():
            label = f"{member.display_name} (@{member.name})"
        result.append(app_commands.Choice(name=label[:100], value=str(member.id)))
        if len(result) >= 25:
            break
    return result


async def registered_driver_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    data = load_data()
    drivers = get_guild_drivers(data, interaction.guild_id)
    query = current.casefold().strip()
    result: list[app_commands.Choice[str]] = []
    for driver in sorted(drivers, key=lambda d: d.get("name", "").casefold()):
        member = interaction.guild.get_member(int(driver["user_id"])) if interaction.guild else None
        name = member.display_name if member else driver.get("name", str(driver["user_id"]))
        team = driver.get("team", "Frei")
        haystack = f"{name} {team}".casefold()
        if query and query not in haystack:
            continue
        result.append(app_commands.Choice(name=f"{name} • {team}"[:100], value=str(driver["user_id"])))
        if len(result) >= 25:
            break
    return result


def format_driver_line(driver: dict) -> str:
    return (
        f"**{driver['name']}** — {driver['team']} / "
        f"{driver['contract']} / {driver['market_value']}"
    )


class TransferBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
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
            color=WTV_COLOR,
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
            color=WTV_COLOR,
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


# =========================================================
# FAHRERDATEN / EINGABEHILFEN
# =========================================================

EDIT_FIELDS = {
    "market_value": ("Marktwert", "market_value", "money"),
    "contract": ("Vertragslaufzeit", "contract", "contract"),
    "salary": ("Gehalt", "salary", "salary"),
    "release_clause": ("Ablöse-/Ausstiegsklausel", "release_clause", "money"),
    "bonuses": ("Boni", "bonuses", "text"),
    "driver_exit_clause": ("Fahrer-Ausstiegsklausel", "driver_exit_clause", "text"),
    "team_termination_clause": ("Team-Kündigungsklausel", "team_termination_clause", "text"),
}

AUDIT_ACTION_FOR_FIELD = {
    "market_value": "market_value_changed",
    "contract": "contract_changed",
    "salary": "salary_changed",
    "release_clause": "release_clause_changed",
    "bonuses": "bonuses_changed",
    "driver_exit_clause": "driver_exit_clause_changed",
    "team_termination_clause": "team_termination_clause_changed",
}


def format_field_input(field_key: str, raw: str) -> str:
    kind = EDIT_FIELDS[field_key][2]
    if kind == "money":
        return normalize_money(raw)
    if kind == "salary":
        return normalize_money(raw, salary=True)
    if kind == "contract":
        return normalize_contract(raw)
    value = raw.strip()
    return value if value else "-"


class SpecialClausesModal(discord.ui.Modal, title="Sonderklauseln"):
    driver_exit = discord.ui.TextInput(
        label="Fahrer-Ausstiegsklausel",
        placeholder="z. B. Ausstieg möglich, wenn Team nur P6 oder schlechter",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )
    team_termination = discord.ui.TextInput(
        label="Team-Kündigungsklausel",
        placeholder="z. B. Team darf bei bestimmten Leistungsbedingungen kündigen",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, driver_id: int, actor_id: int):
        super().__init__()
        self.driver_id = driver_id
        self.actor_id = actor_id
        data = load_data()
        # defaults werden absichtlich nicht dynamisch gesetzt, damit das Formular übersichtlich bleibt.

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("❌ Dieses Formular gehört nicht dir.", ephemeral=True)
            return

        async with data_lock:
            data = load_data()
            drivers = get_guild_drivers(data, interaction.guild_id)
            driver = find_driver(drivers, self.driver_id)
            if driver is None:
                await interaction.response.send_message("❌ Fahrer wurde nicht gefunden.", ephemeral=True)
                return

            driver["driver_exit_clause"] = self.driver_exit.value.strip() or "-"
            driver["team_termination_clause"] = self.team_termination.value.strip() or "-"
            add_audit(
                data,
                interaction.guild_id,
                interaction.user.id,
                "special_clauses_changed",
                f"Sonderklauseln von {driver['name']} aktualisiert.",
            )
            save_data(data)

        await interaction.response.send_message(
            "✅ Sonderklauseln wurden gespeichert.",
            ephemeral=True,
        )


class SpecialClausesView(discord.ui.View):
    def __init__(self, driver_id: int, actor_id: int):
        super().__init__(timeout=180)
        self.driver_id = driver_id
        self.actor_id = actor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("❌ Diese Auswahl gehört nicht dir.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Sonderklauseln hinzufügen", emoji="📝", style=discord.ButtonStyle.secondary)
    async def clauses(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SpecialClausesModal(self.driver_id, self.actor_id))


class AddDriverModal(discord.ui.Modal, title="Fahrer hinzufügen"):
    market_value = discord.ui.TextInput(
        label="Marktwert in Mio. €",
        placeholder="z. B. 25 oder 12,5",
        required=True,
        max_length=30,
    )
    contract = discord.ui.TextInput(
        label="Vertragslaufzeit in Saisons",
        placeholder="z. B. 3",
        required=True,
        max_length=10,
    )
    salary = discord.ui.TextInput(
        label="Gehalt in Mio. € pro Saison",
        placeholder="z. B. 8 oder 7,5",
        required=True,
        max_length=30,
    )
    release_clause = discord.ui.TextInput(
        label="Ablöse-/Ausstiegsklausel",
        placeholder="z. B. 40 Mio. € oder Keine",
        required=False,
        max_length=120,
    )
    bonuses = discord.ui.TextInput(
        label="Boni",
        placeholder="frei wählbar, z. B. 1 Mio. € pro Sieg",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=300,
    )

    def __init__(self, member: discord.Member, actor_id: int):
        super().__init__()
        self.member = member
        self.actor_id = actor_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("❌ Dieses Formular gehört nicht dir.", ephemeral=True)
            return

        try:
            market_value = normalize_money(self.market_value.value)
            contract = normalize_contract(self.contract.value)
            salary = normalize_money(self.salary.value, salary=True)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        team = get_team_from_member(self.member)
        async with data_lock:
            data = load_data()
            drivers = get_guild_drivers(data, interaction.guild_id)
            if find_driver(drivers, self.member.id) is not None:
                await interaction.response.send_message(
                    "❌ Dieser Fahrer ist bereits eingetragen. Nutze dafür `/editdriver`.",
                    ephemeral=True,
                )
                return

            driver = {
                "user_id": self.member.id,
                "name": self.member.display_name,
                "team": team,
                "market_value": market_value,
                "contract": contract,
                "release_clause": self.release_clause.value.strip() or "-",
                "salary": salary,
                "bonuses": self.bonuses.value.strip() or "-",
                "driver_exit_clause": "-",
                "team_termination_clause": "-",
            }
            drivers.append(driver)
            add_audit(
                data,
                interaction.guild_id,
                interaction.user.id,
                "driver_added",
                f"{self.member.display_name} wurde zu {team} hinzugefügt.",
            )
            save_data(data)

        embed = discord.Embed(
            title="✅ Fahrer hinzugefügt",
            description=f"{self.member.mention} wurde als **{display_team(team)}** eingetragen.",
            color=SUCCESS_COLOR,
        )
        embed.add_field(name="Marktwert", value=market_value)
        embed.add_field(name="Vertrag", value=contract)
        embed.add_field(name="Gehalt", value=salary)
        embed.set_footer(text=f"WTV Driver Market • {BOT_VERSION}")
        await interaction.response.send_message(
            embed=embed,
            view=SpecialClausesView(self.member.id, self.actor_id),
            ephemeral=True,
        )


class EditFieldModal(discord.ui.Modal):
    def __init__(self, driver_id: int, actor_id: int, field_key: str, current_value: str):
        label = EDIT_FIELDS[field_key][0]
        super().__init__(title=f"{label} bearbeiten")
        self.driver_id = driver_id
        self.actor_id = actor_id
        self.field_key = field_key
        placeholder = "Neuen Wert eingeben"
        if EDIT_FIELDS[field_key][2] == "money":
            placeholder = "z. B. 25 oder 12,5"
        elif EDIT_FIELDS[field_key][2] == "salary":
            placeholder = "z. B. 8 oder 7,5"
        elif EDIT_FIELDS[field_key][2] == "contract":
            placeholder = "Anzahl Saisons, z. B. 3"
        self.value_input = discord.ui.TextInput(
            label=label[:45],
            placeholder=placeholder,
            default=str(current_value)[:4000] if current_value not in {None, "-"} else None,
            required=True,
            style=discord.TextStyle.paragraph if EDIT_FIELDS[field_key][2] == "text" else discord.TextStyle.short,
            max_length=500 if EDIT_FIELDS[field_key][2] == "text" else 100,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("❌ Dieses Formular gehört nicht dir.", ephemeral=True)
            return
        try:
            new_value = format_field_input(self.field_key, self.value_input.value)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        async with data_lock:
            data = load_data()
            drivers = get_guild_drivers(data, interaction.guild_id)
            driver = find_driver(drivers, self.driver_id)
            if driver is None:
                await interaction.response.send_message("❌ Fahrer wurde nicht gefunden.", ephemeral=True)
                return
            old_value = driver.get(EDIT_FIELDS[self.field_key][1], "-")
            driver[EDIT_FIELDS[self.field_key][1]] = new_value
            add_audit(
                data,
                interaction.guild_id,
                interaction.user.id,
                AUDIT_ACTION_FOR_FIELD[self.field_key],
                f"{driver['name']}: {EDIT_FIELDS[self.field_key][0]} von '{old_value}' auf '{new_value}' geändert.",
            )
            save_data(data)

        await interaction.response.send_message(
            f"✅ **{EDIT_FIELDS[self.field_key][0]}** wurde auf **{new_value}** geändert.",
            ephemeral=True,
        )


class EditDriverView(discord.ui.View):
    def __init__(self, driver_id: int, actor_id: int):
        super().__init__(timeout=180)
        self.driver_id = driver_id
        self.actor_id = actor_id
        options = [
            discord.SelectOption(label=label, value=key)
            for key, (label, _, _) in EDIT_FIELDS.items()
        ]
        self.select = discord.ui.Select(
            placeholder="Was möchtest du ändern?",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("❌ Diese Auswahl gehört nicht dir.", ephemeral=True)
            return False
        return True

    async def select_callback(self, interaction: discord.Interaction) -> None:
        field_key = self.select.values[0]
        data = load_data()
        driver = find_driver(get_guild_drivers(data, interaction.guild_id), self.driver_id)
        if driver is None:
            await interaction.response.send_message("❌ Fahrer wurde nicht gefunden.", ephemeral=True)
            return
        db_key = EDIT_FIELDS[field_key][1]
        await interaction.response.send_modal(
            EditFieldModal(self.driver_id, self.actor_id, field_key, driver.get(db_key, "-"))
        )


class TransferDriverModal(discord.ui.Modal, title="Transferdaten"):
    market_value = discord.ui.TextInput(
        label="Neuer Marktwert (optional)",
        placeholder="leer = unverändert, sonst z. B. 25",
        required=False,
        max_length=30,
    )
    contract = discord.ui.TextInput(
        label="Neue Vertragslaufzeit (optional)",
        placeholder="leer = unverändert, sonst Anzahl Saisons",
        required=False,
        max_length=20,
    )
    salary = discord.ui.TextInput(
        label="Neues Gehalt (optional)",
        placeholder="leer = unverändert, sonst z. B. 8",
        required=False,
        max_length=30,
    )
    release_clause = discord.ui.TextInput(
        label="Neue Ablöse-/Ausstiegsklausel",
        placeholder="leer = unverändert",
        required=False,
        max_length=120,
    )
    bonuses = discord.ui.TextInput(
        label="Neue Boni (optional)",
        placeholder="leer = unverändert",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=300,
    )

    def __init__(self, member: discord.Member, target_team: str, actor_id: int):
        super().__init__()
        self.member = member
        self.target_team = target_team
        self.actor_id = actor_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("❌ Dieses Formular gehört nicht dir.", ephemeral=True)
            return

        data = load_data()
        driver = find_driver(get_guild_drivers(data, interaction.guild_id), self.member.id)
        if driver is None:
            await interaction.response.send_message("❌ Fahrer wurde nicht gefunden.", ephemeral=True)
            return
        old_team = driver.get("team", "Frei")
        if old_team == self.target_team:
            await interaction.response.send_message(
                "❌ Der Fahrer ist bereits in diesem Team. Für Vertragsdaten nutze `/editdriver`.",
                ephemeral=True,
            )
            return

        updates: dict[str, str] = {}
        try:
            if self.market_value.value.strip():
                updates["market_value"] = normalize_money(self.market_value.value)
            if self.contract.value.strip():
                updates["contract"] = normalize_contract(self.contract.value)
            if self.salary.value.strip():
                updates["salary"] = normalize_money(self.salary.value, salary=True)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        if self.release_clause.value.strip():
            updates["release_clause"] = self.release_clause.value.strip()
        if self.bonuses.value.strip():
            updates["bonuses"] = self.bonuses.value.strip()

        role_ok, role_error = await change_member_team_role(self.member, self.target_team)
        if not role_ok:
            await interaction.response.send_message(f"❌ Transfer nicht durchgeführt.\n{role_error}", ephemeral=True)
            return

        async with data_lock:
            data = load_data()
            drivers = get_guild_drivers(data, interaction.guild_id)
            driver = find_driver(drivers, self.member.id)
            if driver is None:
                await interaction.response.send_message("❌ Fahrer wurde während des Transfers nicht gefunden.", ephemeral=True)
                return

            old_team = driver.get("team", "Frei")
            driver["name"] = self.member.display_name
            driver["team"] = self.target_team

            for key, new_value in updates.items():
                old_value = driver.get(key, "-")
                driver[key] = new_value
                action = AUDIT_ACTION_FOR_FIELD.get(key)
                if action:
                    label = next((v[0] for k, v in EDIT_FIELDS.items() if v[1] == key), key)
                    add_audit(
                        data,
                        interaction.guild_id,
                        interaction.user.id,
                        action,
                        f"{driver['name']} beim Transfer: {label} von '{old_value}' auf '{new_value}' geändert.",
                    )

            season = get_guild_season(data, interaction.guild_id)
            transfers = get_guild_transfers(data, interaction.guild_id)
            transfers.append({
                "timestamp": int(time.time()),
                "season": season,
                "driver_id": self.member.id,
                "driver_name": self.member.display_name,
                "from_team": old_team,
                "to_team": self.target_team,
                "actor_id": interaction.user.id,
            })
            add_audit(
                data,
                interaction.guild_id,
                interaction.user.id,
                "transfer",
                f"{self.member.display_name}: {old_team} → {self.target_team}.",
                season=season,
            )
            save_data(data)

        embed = discord.Embed(
            title="🔄 Transfer durchgeführt",
            description=(
                f"**Fahrer:** {self.member.mention}\n"
                f"**Von:** {display_team(old_team)}\n"
                f"**Zu:** {display_team(self.target_team)}"
            ),
            color=SUCCESS_COLOR,
        )
        if updates:
            embed.add_field(
                name="Zusätzlich geändert",
                value="\n".join(f"**{k}:** {v}" for k, v in updates.items()),
                inline=False,
            )
        embed.set_footer(text=f"WTV Driver Market • {BOT_VERSION}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RemoveDriverView(discord.ui.View):
    def __init__(self, driver_id: int, driver_name: str, actor_id: int):
        super().__init__(timeout=90)
        self.driver_id = driver_id
        self.driver_name = driver_name
        self.actor_id = actor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("❌ Diese Bestätigung gehört nicht dir.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Entfernen", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with data_lock:
            data = load_data()
            drivers = get_guild_drivers(data, interaction.guild_id)
            driver = find_driver(drivers, self.driver_id)
            if driver is None:
                await interaction.response.send_message("❌ Fahrer ist bereits entfernt.", ephemeral=True)
                return
            drivers.remove(driver)
            add_audit(
                data,
                interaction.guild_id,
                interaction.user.id,
                "driver_removed",
                f"{self.driver_name} wurde aus dem Fahrermarkt entfernt.",
            )
            save_data(data)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"🗑️ **{self.driver_name}** wurde entfernt.",
            embed=None,
            view=self,
        )

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="❎ Entfernen abgebrochen.", embed=None, view=self)


# =========================================================
# ÖFFENTLICHE COMMANDS
# =========================================================

@bot.tree.command(name="driverinfo", description="Zeigt alle Vertragsinformationen eines Fahrers an.")
@app_commands.describe(fahrer="Fahrer suchen")
@app_commands.autocomplete(fahrer=registered_driver_autocomplete)
@app_commands.guild_only()
async def driverinfo(interaction: discord.Interaction, fahrer: str) -> None:
    user_id = parse_user_id(fahrer)
    if user_id is None:
        await interaction.response.send_message("❌ Ungültiger Fahrer.", ephemeral=True)
        return

    data = load_data()
    drivers = get_guild_drivers(data, interaction.guild_id)
    season = get_guild_season(data, interaction.guild_id)
    driver = find_driver(drivers, user_id)
    if driver is None:
        await interaction.response.send_message(
            "❌ Dieser Fahrer ist nicht im Fahrermarkt eingetragen.",
            ephemeral=True,
        )
        return

    member = await resolve_member(interaction, fahrer)
    mention = member.mention if member else f"<@{user_id}>"
    name = member.display_name if member else driver.get("name", str(user_id))

    embed = discord.Embed(
        title=f"🏁 WTV Fahrerinfo — {name}",
        description=mention,
        color=WTV_COLOR,
    )

    embed.add_field(
        name="Team",
        value=display_team(driver.get("team", "Frei")),
        inline=True,
    )
    embed.add_field(
        name="Marktwert",
        value=driver.get("market_value", "-"),
        inline=True,
    )

    # Gewünschte Reihenfolge
    embed.add_field(name="📄 Vertrag", value=driver.get("contract", "-"), inline=False)
    embed.add_field(name="💶 Gehalt", value=driver.get("salary", "-"), inline=False)
    embed.add_field(name="🎁 Boni", value=driver.get("bonuses", "-"), inline=False)

    # Gespeicherte Klauseln erscheinen automatisch in /driverinfo.
    embed.add_field(
        name="🔓 Ablöse-/Ausstiegsklausel",
        value=driver.get("release_clause", "-"),
        inline=False,
    )
    embed.add_field(
        name="🚪 Fahrer-Ausstiegsklausel",
        value=driver.get("driver_exit_clause", "-"),
        inline=False,
    )
    embed.add_field(
        name="🏢 Team-Kündigungsklausel",
        value=driver.get("team_termination_clause", "-"),
        inline=False,
    )

    embed.set_footer(text=f"WTV Driver Market • {BOT_VERSION} • Saison {season}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="Zeigt alle verfügbaren Commands und deren Berechtigungen.")
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction) -> None:
    season = get_guild_season(load_data(), interaction.guild_id)
    is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
    embed = discord.Embed(
        title="📖 WTV DRIVER MARKET • HELP",
        description=(
            f"**{BOT_VERSION}** • Saison {season}\n"
            f"Dein Zugriff: {'✅ Administrator' if is_admin else '🌍 Öffentlich'}"
        ),
        color=WTV_COLOR,
    )
    embed.add_field(
        name="🌍 Öffentlich",
        value=(
            "`/drivermarket` • `/alldrivers` • `/driverinfo`\n"
            "`/seasonstatus` • `/version` • `/help`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ Administrator",
        value=(
            "`/adddriver` • `/editdriver` • `/transferdriver`\n"
            "`/removedriver` • `/saison` • `/datastatus` • `/auditlog`"
        ),
        inline=False,
    )
    embed.set_footer(text=f"WTV Driver Market • {BOT_VERSION}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="version", description="Zeigt die aktuell laufende Bot-Version an.")
@app_commands.guild_only()
async def version(interaction: discord.Interaction) -> None:
    persistent = storage_is_persistent()
    embed = discord.Embed(title=f"🤖 WTV Driver Market {BOT_VERSION}", color=WTV_COLOR)
    embed.add_field(name="Version", value="✅ Stabile Hauptversion", inline=True)
    embed.add_field(name="Speicher", value="✅ Railway Volume" if persistent else "⚠️ Lokaler Speicher", inline=True)
    embed.add_field(name="Commands", value=str(len(bot.tree.get_commands())), inline=True)
    embed.set_footer(text="WTV Driver Market • stabile Hauptversion")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="seasonstatus", description="Zeigt Änderungen und Transfers einer Saison an.")
@app_commands.describe(saison="Optional: Saisonnummer; leer = aktuelle Saison")
@app_commands.guild_only()
async def seasonstatus(interaction: discord.Interaction, saison: app_commands.Range[int, 1, 999] | None = None) -> None:
    data = load_data()
    target = int(saison) if saison is not None else get_guild_season(data, interaction.guild_id)
    audit = [e for e in get_guild_audit(data, interaction.guild_id) if int(e.get("season", 0)) == target]
    transfers = [e for e in get_guild_transfers(data, interaction.guild_id) if int(e.get("season", 0)) == target]

    def count(action: str) -> int:
        return sum(1 for e in audit if e.get("action") == action)

    embed = discord.Embed(
        title=f"🏁 Saison {target} • Status",
        description="Zusammenfassung der bisher protokollierten Änderungen.",
        color=WTV_COLOR,
    )
    embed.add_field(name="🔄 Teamwechsel", value=str(len(transfers)), inline=True)
    embed.add_field(name="💰 Marktwertänderungen", value=str(count("market_value_changed")), inline=True)
    embed.add_field(name="📄 Vertragsänderungen", value=str(count("contract_changed")), inline=True)
    embed.add_field(name="➕ Fahrer hinzugefügt", value=str(count("driver_added")), inline=True)
    embed.add_field(name="➖ Fahrer entfernt", value=str(count("driver_removed")), inline=True)
    embed.add_field(name="📝 Audit-Einträge", value=str(len(audit)), inline=True)
    if not audit and not transfers:
        embed.add_field(
            name="Hinweis",
            value="Für diese Saison wurden noch keine Änderungen protokolliert.",
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


# =========================================================
# ADMINISTRATOR-COMMANDS
# =========================================================

@bot.tree.command(name="saison", description="Setzt die aktuelle Saisonnummer direkt.")
@app_commands.describe(nummer="Saisonnummer, z. B. 6")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def saison(interaction: discord.Interaction, nummer: app_commands.Range[int, 1, 999]) -> None:
    async with data_lock:
        data = load_data()
        old_season = get_guild_season(data, interaction.guild_id)
        set_guild_season(data, interaction.guild_id, nummer)
        add_audit(
            data,
            interaction.guild_id,
            interaction.user.id,
            "season_set",
            f"Saisonnummer von {old_season} auf {nummer} gesetzt.",
            season=nummer,
        )
        save_data(data)
    embed = discord.Embed(
        title="🏁 Saison gesetzt",
        description=f"**Saison {old_season} → Saison {nummer}**\n\nVertragslaufzeiten wurden nicht automatisch verändert.",
        color=SUCCESS_COLOR,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="adddriver",
    description="Fügt einen Fahrer direkt hinzu – ohne Popup-Fenster.",
)
@app_commands.describe(
    fahrer="Servermitglied suchen",
    marktwert="Marktwert in Mio., z. B. 25 oder 12,5",
    vertrag="Vertragslaufzeit in Saisons, 0 = Kein Vertrag",
    ausstiegsklausel="Ablöse-/Ausstiegsklausel in Mio., z. B. 50; '-' = keine",
    gehalt="Gehalt in Mio. pro Saison, z. B. 8 oder 7,5",
    boni="Boni frei eintragen; '-' = keine",
    fahrer_ausstieg="Optional: Bedingung, unter der der Fahrer aussteigen darf",
    team_kuendigung="Optional: Bedingung, unter der das Team kündigen darf",
)
@app_commands.autocomplete(fahrer=guild_member_autocomplete)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def adddriver(
    interaction: discord.Interaction,
    fahrer: str,
    marktwert: str,
    vertrag: app_commands.Range[int, 0, 99],
    ausstiegsklausel: str,
    gehalt: str,
    boni: str,
    fahrer_ausstieg: str | None = None,
    team_kuendigung: str | None = None,
) -> None:
    member = await resolve_member(interaction, fahrer)

    if member is None or member.bot:
        await interaction.response.send_message(
            "❌ Mitglied nicht gefunden. Prüfe auch, ob **Server Members Intent** aktiviert ist.",
            ephemeral=True,
        )
        return

    try:
        market_value = format_field_input("market_value", marktwert)
        contract = format_field_input("contract", str(vertrag))
        release_clause = format_field_input("release_clause", ausstiegsklausel)
        salary = format_field_input("salary", gehalt)
        bonuses = format_field_input("bonuses", boni)
        driver_exit_clause = (
            format_field_input("driver_exit_clause", fahrer_ausstieg)
            if fahrer_ausstieg is not None
            else "-"
        )
        team_termination_clause = (
            format_field_input("team_termination_clause", team_kuendigung)
            if team_kuendigung is not None
            else "-"
        )
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return

    team = get_team_from_member(member)

    async with data_lock:
        data = load_data()
        drivers = get_guild_drivers(data, interaction.guild_id)
        existing = find_driver(drivers, member.id)

        new_values = {
            "user_id": member.id,
            "name": member.display_name,
            "team": team,
            "market_value": market_value,
            "contract": contract,
            "release_clause": release_clause,
            "salary": salary,
            "bonuses": bonuses,
            "driver_exit_clause": driver_exit_clause,
            "team_termination_clause": team_termination_clause,
        }

        if existing is None:
            drivers.append(new_values)
            title = "➕ Fahrer hinzugefügt"
            audit_action = "driver_added"
            audit_details = (
                f"{member.display_name} wurde hinzugefügt "
                f"({team}, Marktwert {market_value}, Vertrag {contract})."
            )
        else:
            existing.update(new_values)
            title = "♻️ Fahrer aktualisiert"
            audit_action = "driver_edited"
            audit_details = f"{member.display_name} wurde über /adddriver aktualisiert."

        add_audit(
            data,
            interaction.guild_id,
            interaction.user.id,
            audit_action,
            audit_details,
        )
        save_data(data)

    embed = discord.Embed(
        title=title,
        description=f"**Fahrer:** {member.mention}",
        color=SUCCESS_COLOR,
    )
    embed.add_field(name="Team", value=display_team(team), inline=True)
    embed.add_field(name="Marktwert", value=market_value, inline=True)
    embed.add_field(name="📄 Vertrag", value=contract, inline=False)
    embed.add_field(name="💶 Gehalt", value=salary, inline=False)
    embed.add_field(name="🎁 Boni", value=bonuses, inline=False)
    embed.add_field(
        name="🔓 Ablöse-/Ausstiegsklausel",
        value=release_clause,
        inline=False,
    )

    if driver_exit_clause != "-":
        embed.add_field(
            name="🚪 Fahrer-Ausstiegsklausel",
            value=driver_exit_clause,
            inline=False,
        )

    if team_termination_clause != "-":
        embed.add_field(
            name="🏢 Team-Kündigungsklausel",
            value=team_termination_clause,
            inline=False,
        )

    embed.set_footer(text=f"WTV Driver Market • {BOT_VERSION}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="editdriver",
    description="Bearbeitet Fahrerdaten direkt in einem Command – ohne Popup-Fenster.",
)
@app_commands.describe(
    fahrer="Eingetragenen Fahrer suchen",
    marktwert="Optional: z. B. 25 oder 12,5",
    vertrag="Optional: Laufzeit in Saisons, 0 = Kein Vertrag",
    gehalt="Optional: z. B. 8 oder 7,5 Mio. pro Saison",
    ausstiegsklausel="Optional: Ablöse-/Ausstiegsklausel; '-' = keine",
    boni="Optional: Boni frei eintragen; '-' = keine",
    fahrer_ausstieg="Optional: Bedingung für vorzeitigen Ausstieg des Fahrers",
    team_kuendigung="Optional: Bedingung für vorzeitige Kündigung durch das Team",
)
@app_commands.autocomplete(fahrer=registered_driver_autocomplete)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def editdriver(
    interaction: discord.Interaction,
    fahrer: str,
    marktwert: str | None = None,
    vertrag: app_commands.Range[int, 0, 99] | None = None,
    gehalt: str | None = None,
    ausstiegsklausel: str | None = None,
    boni: str | None = None,
    fahrer_ausstieg: str | None = None,
    team_kuendigung: str | None = None,
) -> None:
    user_id = parse_user_id(fahrer)
    if user_id is None:
        await interaction.response.send_message("❌ Ungültiger Fahrer.", ephemeral=True)
        return

    raw_updates = {
        "market_value": marktwert,
        "contract": str(vertrag) if vertrag is not None else None,
        "salary": gehalt,
        "release_clause": ausstiegsklausel,
        "bonuses": boni,
        "driver_exit_clause": fahrer_ausstieg,
        "team_termination_clause": team_kuendigung,
    }

    if not any(value is not None for value in raw_updates.values()):
        await interaction.response.send_message(
            "ℹ️ Du hast keine Änderung angegeben. "
            "Fülle mindestens eines der optionalen Felder aus.",
            ephemeral=True,
        )
        return

    formatted_updates: dict[str, str] = {}
    try:
        for field_key, raw_value in raw_updates.items():
            if raw_value is None:
                continue
            formatted_updates[field_key] = format_field_input(field_key, str(raw_value))
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return

    changed_lines: list[str] = []

    async with data_lock:
        data = load_data()
        drivers = get_guild_drivers(data, interaction.guild_id)
        driver = find_driver(drivers, user_id)
        if driver is None:
            await interaction.response.send_message("❌ Fahrer wurde nicht gefunden.", ephemeral=True)
            return

        for field_key, new_value in formatted_updates.items():
            db_key = EDIT_FIELDS[field_key][1]
            label = EDIT_FIELDS[field_key][0]
            old_value = str(driver.get(db_key, "-"))

            if old_value == new_value:
                continue

            driver[db_key] = new_value
            changed_lines.append(f"**{label}:** {old_value} → {new_value}")

            add_audit(
                data,
                interaction.guild_id,
                interaction.user.id,
                AUDIT_ACTION_FOR_FIELD[field_key],
                f"{driver['name']}: {label} von '{old_value}' auf '{new_value}' geändert.",
            )

        if changed_lines:
            save_data(data)

    if not changed_lines:
        await interaction.response.send_message(
            "ℹ️ Die angegebenen Werte entsprechen bereits den gespeicherten Daten.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="✏️ Fahrer aktualisiert",
        description="\n".join(changed_lines),
        color=SUCCESS_COLOR,
    )
    embed.set_footer(text=f"WTV Driver Market • {BOT_VERSION}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="transferdriver",
    description="Transferiert einen Fahrer direkt – optionale Änderungen ohne Popup.",
)
@app_commands.describe(
    fahrer="Eingetragenen Fahrer suchen",
    neues_team="Neues Team oder Free Agent",
    marktwert="Optional: neuer Marktwert, z. B. 25",
    vertrag="Optional: neue Laufzeit in Saisons, 0 = Kein Vertrag",
    gehalt="Optional: neues Gehalt, z. B. 8 oder 7,5",
    ausstiegsklausel="Optional: neue Ablöse-/Ausstiegsklausel",
    boni="Optional: neue Boni",
    fahrer_ausstieg="Optional: neue Fahrer-Ausstiegsklausel",
    team_kuendigung="Optional: neue Team-Kündigungsklausel",
)
@app_commands.autocomplete(fahrer=registered_driver_autocomplete)
@app_commands.choices(neues_team=TRANSFER_TEAM_CHOICES)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def transferdriver(
    interaction: discord.Interaction,
    fahrer: str,
    neues_team: app_commands.Choice[str],
    marktwert: str | None = None,
    vertrag: app_commands.Range[int, 0, 99] | None = None,
    gehalt: str | None = None,
    ausstiegsklausel: str | None = None,
    boni: str | None = None,
    fahrer_ausstieg: str | None = None,
    team_kuendigung: str | None = None,
) -> None:
    member = await resolve_member(interaction, fahrer)
    if member is None:
        await interaction.response.send_message(
            "❌ Der Fahrer ist aktuell nicht als Servermitglied erreichbar.",
            ephemeral=True,
        )
        return

    data = load_data()
    current_driver = find_driver(
        get_guild_drivers(data, interaction.guild_id),
        member.id,
    )
    if current_driver is None:
        await interaction.response.send_message(
            "❌ Fahrer wurde nicht im Fahrermarkt gefunden.",
            ephemeral=True,
        )
        return

    target_team = neues_team.value
    old_team = current_driver.get("team", "Frei")
    if old_team == target_team:
        await interaction.response.send_message(
            "❌ Der Fahrer ist bereits in diesem Team. "
            "Für Vertrags- oder Marktwertänderungen nutze `/editdriver`.",
            ephemeral=True,
        )
        return

    raw_updates = {
        "market_value": marktwert,
        "contract": str(vertrag) if vertrag is not None else None,
        "salary": gehalt,
        "release_clause": ausstiegsklausel,
        "bonuses": boni,
        "driver_exit_clause": fahrer_ausstieg,
        "team_termination_clause": team_kuendigung,
    }

    formatted_updates: dict[str, str] = {}
    try:
        for field_key, raw_value in raw_updates.items():
            if raw_value is None:
                continue
            formatted_updates[field_key] = format_field_input(field_key, str(raw_value))
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        return

    role_ok, role_error = await change_member_team_role(member, target_team)
    if not role_ok:
        await interaction.response.send_message(
            f"❌ Transfer nicht durchgeführt.\n{role_error}",
            ephemeral=True,
        )
        return

    changed_lines: list[str] = []

    async with data_lock:
        data = load_data()
        drivers = get_guild_drivers(data, interaction.guild_id)
        driver = find_driver(drivers, member.id)
        if driver is None:
            await interaction.response.send_message(
                "❌ Fahrer wurde während des Transfers nicht gefunden.",
                ephemeral=True,
            )
            return

        old_team = driver.get("team", "Frei")
        driver["name"] = member.display_name
        driver["team"] = target_team

        for field_key, new_value in formatted_updates.items():
            db_key = EDIT_FIELDS[field_key][1]
            label = EDIT_FIELDS[field_key][0]
            old_value = str(driver.get(db_key, "-"))

            if old_value == new_value:
                continue

            driver[db_key] = new_value
            changed_lines.append(f"**{label}:** {old_value} → {new_value}")

            add_audit(
                data,
                interaction.guild_id,
                interaction.user.id,
                AUDIT_ACTION_FOR_FIELD[field_key],
                f"{driver['name']} beim Transfer: {label} von '{old_value}' auf '{new_value}' geändert.",
            )

        season = get_guild_season(data, interaction.guild_id)
        transfers = get_guild_transfers(data, interaction.guild_id)
        transfers.append({
            "timestamp": int(time.time()),
            "season": season,
            "driver_id": member.id,
            "driver_name": member.display_name,
            "from_team": old_team,
            "to_team": target_team,
            "actor_id": interaction.user.id,
        })

        add_audit(
            data,
            interaction.guild_id,
            interaction.user.id,
            "transfer",
            f"{member.display_name}: {old_team} → {target_team}.",
            season=season,
        )
        save_data(data)

    embed = discord.Embed(
        title="🔄 Transfer durchgeführt",
        description=(
            f"**Fahrer:** {member.mention}\n"
            f"**Von:** {display_team(old_team)}\n"
            f"**Zu:** {display_team(target_team)}"
        ),
        color=SUCCESS_COLOR,
    )

    if changed_lines:
        embed.add_field(
            name="Zusätzlich geändert",
            value="\n".join(changed_lines),
            inline=False,
        )

    embed.set_footer(text=f"WTV Driver Market • {BOT_VERSION}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="removedriver", description="Entfernt einen Fahrer nach einer Sicherheitsabfrage.")
@app_commands.describe(fahrer="Eingetragenen Fahrer suchen")
@app_commands.autocomplete(fahrer=registered_driver_autocomplete)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def removedriver(interaction: discord.Interaction, fahrer: str) -> None:
    user_id = parse_user_id(fahrer)
    data = load_data()
    driver = find_driver(get_guild_drivers(data, interaction.guild_id), user_id) if user_id else None
    if driver is None:
        await interaction.response.send_message("❌ Fahrer wurde nicht gefunden.", ephemeral=True)
        return
    embed = discord.Embed(
        title="⚠️ Fahrer wirklich entfernen?",
        description=f"**{driver['name']}** wird aus dem Fahrermarkt entfernt. Die Audit-Historie bleibt erhalten.",
        color=DANGER_COLOR,
    )
    await interaction.response.send_message(
        embed=embed,
        view=RemoveDriverView(user_id, driver['name'], interaction.user.id),
        ephemeral=True,
    )


@bot.tree.command(name="datastatus", description="Zeigt Datenbestand und Railway-Speicherstatus an.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def datastatus(interaction: discord.Interaction) -> None:
    data = load_data()
    drivers = get_guild_drivers(data, interaction.guild_id)
    transfers = get_guild_transfers(data, interaction.guild_id)
    audit = get_guild_audit(data, interaction.guild_id)
    persistent = storage_is_persistent()
    file_exists = DATA_FILE.exists()
    backup_exists = BACKUP_FILE.exists()
    size = DATA_FILE.stat().st_size if file_exists else 0

    embed = discord.Embed(
        title="💾 WTV Data Status",
        color=SUCCESS_COLOR if persistent else WARNING_COLOR,
    )
    embed.add_field(name="Fahrer", value=str(len(drivers)), inline=True)
    embed.add_field(name="Transfers", value=str(len(transfers)), inline=True)
    embed.add_field(name="Audit-Einträge", value=str(len(audit)), inline=True)
    embed.add_field(name="Saison", value=str(get_guild_season(data, interaction.guild_id)), inline=True)
    embed.add_field(name="Datendatei", value="✅ vorhanden" if file_exists else "🆕 noch nicht angelegt", inline=True)
    embed.add_field(name="Dateigröße", value=f"{size / 1024:.1f} KB", inline=True)
    embed.add_field(name="Backup", value="✅ vorhanden" if backup_exists else "🆕 noch keines", inline=True)
    embed.add_field(
        name="Railway Volume",
        value="✅ Aktiv – persistenter Speicher" if persistent else "⚠️ Nicht erkannt – aktuell nur lokaler Speicher",
        inline=False,
    )
    embed.add_field(name="Speicherpfad", value=f"`{DATA_FILE}`", inline=False)
    embed.add_field(name="Backup-Pfad", value=f"`{BACKUP_FILE}`", inline=False)
    if not persistent:
        embed.add_field(
            name="⚠️ Warnung",
            value="Ein Railway Volume auf `/data` mounten. Sonst können Daten bei Deployments verloren gehen.",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="auditlog", description="Zeigt die letzten protokollierten Änderungen an.")
@app_commands.describe(anzahl="Anzahl Einträge, 1 bis 20")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def auditlog(interaction: discord.Interaction, anzahl: app_commands.Range[int, 1, 20] = 10) -> None:
    data = load_data()
    logs = get_guild_audit(data, interaction.guild_id)
    if not logs:
        await interaction.response.send_message("ℹ️ Es gibt noch keine Audit-Einträge.", ephemeral=True)
        return
    lines = []
    for entry in reversed(logs[-int(anzahl):]):
        ts = int(entry.get("timestamp", 0))
        season = entry.get("season", "?")
        actor = entry.get("actor_id")
        details = str(entry.get("details", ""))
        lines.append(f"<t:{ts}:g> • **S{season}** • <@{actor}>\n{details}")
    embed = discord.Embed(
        title="🧾 Audit Log",
        description="\n\n".join(lines),
        color=WTV_COLOR,
    )
    embed.set_footer(text=f"Letzte {min(int(anzahl), len(logs))} Einträge • {BOT_VERSION}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Dafür brauchst du die Discord-Berechtigung **Administrator**."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = "⏳ Bitte warte kurz und versuche es erneut."
    else:
        print(f"Command-Fehler: {error}")
        message = "❌ Beim Ausführen des Commands ist ein Fehler aufgetreten."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.event
async def on_ready() -> None:
    storage = "persistent" if storage_is_persistent() else "lokal / NICHT persistent"
    print(f"WTV Driver Market {BOT_VERSION} online als {bot.user}.")
    print(f"Speicher: {DATA_FILE} ({storage})")


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env-Datei.")

bot.run(TOKEN)
