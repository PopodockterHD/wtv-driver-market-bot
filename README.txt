DISCORD FAHRERMARKT BOT
======================

Enthaltene Slash-Commands:

/drivermarket
- Zeigt den Fahrermarkt nach Teams an.
- Mit Vor/Zurueck-Buttons kann zwischen den Teams gewechselt werden.
- Ein Fahrer kann als freier Fahrer angezeigt werden, wenn bei Team "Frei" eingetragen wird.

/alldrivers
- Zeigt alle Fahrer kompakt an.
- Format: Name — Team / Vertrag / Marktwert
- Bei vielen Fahrern wird automatisch auf mehrere Seiten aufgeteilt.

/driverinfo
- Zeigt die vollstaendigen Daten eines einzelnen Fahrers:
  Team, Marktwert, Vertrag, Ausstiegsklausel, Gehalt und Boni.

/adddriver
- Nur fuer Mitglieder mit "Server verwalten".
- Fuegt einen Fahrer hinzu.
- Ist der Fahrer schon vorhanden, werden seine Daten aktualisiert.
- Fuer einen Free Agent einfach Team = Frei verwenden.

/removedriver
- Nur fuer Mitglieder mit "Server verwalten".
- Entfernt einen Fahrer KOMPLETT aus dem Fahrermarkt.
- Wenn ein Fahrer nur kein Team hat, besser /adddriver verwenden und Team = Frei setzen.

INSTALLATION
============

1. Python installieren.
2. Discord Developer Portal oeffnen und eine neue Application erstellen.
3. Bot erstellen und den Bot-Token kopieren.
4. Den Bot mit den Scopes "bot" und "applications.commands" auf den Server einladen.
5. Diesen Ordner entpacken.
6. Terminal/CMD in diesem Ordner oeffnen.
7. Ausfuehren:

   python -m pip install -r requirements.txt

8. .env.example in .env umbenennen.
9. In .env den Token eintragen:

   DISCORD_TOKEN=DEIN_BOT_TOKEN

10. Bot starten:

   python bot.py

Die Fahrerdaten werden automatisch in driver_market.json gespeichert.
Der Bot muss laufen, damit die Slash-Commands funktionieren.
