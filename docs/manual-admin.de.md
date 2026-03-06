# Taskify – Admin-Handbuch

Admins haben vollen Zugriff auf alles, was Managern und Mitarbeitern zur Verfügung steht, sowie auf die hier beschriebenen Funktionen.

Die **Hilfe**-Seite ermöglicht das Wechseln zwischen allen vier Handbüchern (Mitarbeiter, Manager, Admin, Kunden) über die Schaltflächengruppe oben rechts.

## Inhaltsverzeichnis

1. [Mitarbeiterverwaltung](#1-mitarbeiterverwaltung)
2. [Tickets löschen](#2-tickets-loschen)
3. [GitHub-Integration](#3-github-integration)
4. [MantisBT-Synchronisation](#4-mantisbt-synchronisation)
5. [E-Mail-Test](#5-e-mail-test)
6. [Systemtests](#6-systemtests)
7. [Umgebungsvariablen](#7-umgebungsvariablen)
8. [Eingehende E-Mails](#8-eingehende-e-mails)
9. [Notfallmaßnahmen](#9-notfallmasnahmen)

---

## 1. Mitarbeiterverwaltung

Gehen Sie zu **Admin → Mitarbeiter**.

### Mitarbeiter erstellen

Füllen Sie **Name**, **E-Mail** und **Passwort** aus und setzen Sie bei Bedarf den Haken bei *Admin-Rechte* oder *Manager-Rechte*. Klicken Sie auf **Erstellen**. Der Mitarbeiter kann sich sofort anmelden.

### Rollen

| Rolle | Kann |
|-------|------|
| Staff | Tickets bearbeiten |
| Manager | Tickets bearbeiten + Kunden und Projekte verwalten + Staff bearbeiten |
| Admin | Vollzugriff inkl. Mitarbeiterverwaltung und Systemkonfiguration |

### Bearbeiten

Klicken Sie auf das Stift-Symbol, um Name, E-Mail oder Passwort eines Mitarbeiters zu ändern.

- Admins können jeden Mitarbeiter außer sich selbst bearbeiten.
- Manager können nur Staff-Mitarbeiter bearbeiten.
- Lassen Sie das Passwortfeld leer, um das aktuelle Passwort beizubehalten.
- Um eine Rolle zu ändern, verwenden Sie dasselbe Bearbeitungsformular und setzen oder entfernen Sie die Haken bei *Admin-Rechten* und *Manager-Rechten*. Die eigene Rolle kann nicht geändert werden. Das Entfernen der Admin-Rolle des letzten aktiven Admins ist gesperrt.

### Deaktivieren / Reaktivieren

Klicken Sie auf **Deaktivieren**, um eine Anmeldung zu sperren, ohne das Konto zu löschen. Der Verlauf und die Ticketzuweisungen des Mitarbeiters bleiben erhalten. Klicken Sie auf **Aktivieren**, um den Zugang wiederherzustellen.

Ein Admin kann weder das eigene Konto noch das eines anderen Admins deaktivieren.

### Löschen

Klicken Sie auf das Papierkorb-Symbol, um einen Mitarbeiter dauerhaft zu löschen. Dies kann nicht rückgängig gemacht werden. Der Mitarbeiter muss zuerst deaktiviert werden; das eigene Konto kann nicht gelöscht werden.

---

## 2. Tickets löschen

Öffnen Sie das Ticket und scrollen Sie zum Ende der Seitenleiste. Klicken Sie auf **Ticket löschen** und bestätigen Sie die Abfrage.

Das Löschen entfernt dauerhaft:
- Alle Nachrichten und internen Notizen
- Alle Dateianhänge (aus der Datenbank und vom Datenträger)
- Statusverlauf und Audit-Ereignisse
- Zuweisung und Beobachtungs-Abonnements

Diese Aktion kann nicht rückgängig gemacht werden. Der Einreicher wird nicht benachrichtigt.

---

## 3. GitHub-Integration

Mitarbeiter können sich mit **Mit GitHub anmelden** einloggen, wenn ihr GitHub-Konto verknüpft ist.

### Verknüpfen

Geben Sie auf der Mitarbeiterseite den GitHub-Benutzernamen in der Spalte *GitHub* ein und klicken Sie auf **Verknüpfen**. Die App ruft die GitHub-API auf, um den Benutzernamen zu verifizieren.

### Verknüpfung aufheben

Klicken Sie auf **✕** neben dem verknüpften Benutzernamen. Die Passwort-Anmeldung des Mitarbeiters ist davon nicht betroffen.

### Erforderliche Konfiguration

Setzen Sie folgende Umgebungsvariablen (siehe [Umgebungsvariablen](#7-umgebungsvariablen)):

| Variable | Beschreibung |
|----------|--------------|
| `GITHUB_CLIENT_ID` | Client-ID der OAuth-App aus GitHub |
| `GITHUB_CLIENT_SECRET` | Client-Secret der OAuth-App |

Erstellen Sie die OAuth-App unter *GitHub → Einstellungen → Entwicklereinstellungen → OAuth-Apps*. Setzen Sie die **Autorisierungs-Callback-URL** auf `https://<ihre-domain>/github/callback`.

---

## 4. MantisBT-Synchronisation

Gehen Sie zu **Admin → MantisBT Sync**, um Daten aus einer bestehenden MantisBT-Installation zu importieren.

### Funktionsweise

1. Geben Sie die **MySQL/MariaDB-Verbindungsdaten** Ihrer MantisBT-Datenbank ein und klicken Sie auf **Vorschau laden**.
2. Das Vorschau-Panel zeigt drei Tabs – **Projekte**, **Nutzer** und **Tickets** – jeweils mit Filtereingabe und Auswahlkästchen.
3. Wählen Sie die gewünschten Einträge aus und klicken Sie auf **Testlauf starten** (standardmäßig aktiviert), um zu prüfen was importiert würde, ohne etwas zu speichern.
4. Deaktivieren Sie **Testlauf** und klicken Sie auf **Sync starten**, um die Änderungen zu übernehmen.

### Rollenzuordnung

MantisBT-Zugriffsebenen werden wie folgt auf Taskify-Rollen abgebildet:

| MantisBT-Ebene | Taskify-Rolle |
|----------------|---------------|
| Betrachter / Melder / Aktualisierer | Kunde |
| Entwickler | Mitarbeiter (Staff) |
| Projektleiter | Manager |

### Projektverwaltung

- MantisBT-Projekte werden als Taskify-**Projekte** importiert.
- Projekte, die in Taskify bereits existieren (Abgleich per Name), werden mit dem Badge *Bereits vorhanden* markiert und standardmäßig abgewählt – nur neue Projekte sind vorausgewählt.
- Kunden werden anhand ihrer MantisBT-Projektmitgliedschaften den neu erstellten Projekten zugewiesen. Mitgliedschaften für bereits vorhandene Projekte bleiben unverändert.

### Ticket-Zuordnung

| MantisBT-Status | Taskify-Status |
|-----------------|----------------|
| Neu / Feedback / Bestätigt / Anerkannt | Offen |
| Zugewiesen | In Bearbeitung |
| Gelöst | Gelöst |
| Geschlossen | Geschlossen |

Bereits importierte Tickets (erkennbar am `[mantis:ID]`-Tag im internen Titel) werden übersprungen.

### Testlauf

Das Kontrollkästchen **Testlauf** ist standardmäßig aktiviert. Im Testlauf werden alle Änderungen berechnet, aber nicht in der Datenbank gespeichert, und es werden keine Einrichtungs-E-Mails an neue Nutzer verschickt. Die Ergebnismeldung zeigt, was *passiert wäre*.

### Erforderliche Konfiguration

Die MantisBT-Datenbankverbindung kann über Umgebungsvariablen vorausgefüllt werden:

| Variable | Beschreibung |
|----------|--------------|
| `MANTIS_DB_HOST` | MySQL/MariaDB-Hostname |
| `MANTIS_DB_PORT` | Port (Standard: `3306`) |
| `MANTIS_DB_NAME` | Datenbankname (Standard: `bugtracker`) |
| `MANTIS_DB_USER` | Datenbankbenutzername |
| `MANTIS_DB_PASS` | Datenbankpasswort |
| `MANTIS_TABLE_PREFIX` | Tabellenpräfix (Standard: `mantis_`) |

---

## 5. E-Mail-Test

Gehen Sie zu **Admin → E-Mail-Test**, um eine Test-E-Mail an eine beliebige Adresse zu senden. Damit können Sie überprüfen, ob Ihre SMTP-Konfiguration funktioniert.

Das Ergebnis zeigt, ob die Nachricht vom Server akzeptiert wurde. Prüfen Sie den Posteingang (und Spam-Ordner) des Empfängers zur Bestätigung.

---

## 6. Systemtests

Gehen Sie zu **Admin → Systemtests**, um eine vollständige Zustandsprüfung der Anwendung durchzuführen.

Tests sind in zwei Kategorien unterteilt:

**Infrastrukturprüfungen** — schreibgeschützt, jederzeit sicher durchführbar:
- Datenbankverbindung
- Konfigurationsvollständigkeit (geheimer Schlüssel, Upload-Ordner, App-Name, öffentlicher Ticket-Modus)
- E-Mail-Konfiguration und SMTP-Verbindung
- Konfiguration eingehender E-Mails und Thread-Zustand
- GitHub-OAuth-Konfiguration und API-Erreichbarkeit

**Funktionale Tests** — erstellen und löschen sofort echte Datenbankeinträge:
- Ticket CRUD, Statusübergänge, Antworten, Zuweisung, Beobachten
- Mitarbeiter erstellen, Passwort ändern, Aktivierung umschalten
- Kunden erstellen, Gruppenzugehörigkeit, Aktivierung umschalten

Testdatensätze verwenden die reservierte Domain `@taskify-test.invalid` und werden zu Beginn jedes Durchlaufs bereinigt.

Ergebnisse zeigen **Bestanden**, **Fehlgeschlagen**, **Warnung** oder **Info**. Klicken Sie auf das Pfeilsymbol einer Zeile, um das Schritt-für-Schritt-Protokoll anzuzeigen.

---

## 7. Umgebungsvariablen

Die gesamte Konfiguration erfolgt über Umgebungsvariablen (oder eine `.env`-Datei im Projektstamm).

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `SECRET_KEY` | unsicherer Standard | Flask-Sitzungs-Signaturschlüssel — **muss in der Produktion geändert werden** |
| `DATABASE_URL` | `sqlite:///taskify.db` | SQLAlchemy-Verbindungsstring |
| `UPLOAD_FOLDER` | `uploads/` | Verzeichnis für Ticket-Anhänge |
| `APP_NAME` | `Taskify` | Wird in der Oberfläche und in E-Mails angezeigt |
| `PUBLIC_TICKETS` | `false` | Auf `true` setzen, um anonyme Ticketeinreichung zu erlauben |
| `MAIL_SERVER` | — | SMTP-Hostname |
| `MAIL_PORT` | `587` | SMTP-Port |
| `MAIL_USE_TLS` | `true` | STARTTLS aktivieren |
| `MAIL_USE_SSL` | `false` | SMTP_SSL aktivieren (Alternative zu TLS) |
| `MAIL_USERNAME` | — | SMTP-Authentifizierungsbenutzername |
| `MAIL_PASSWORD` | — | SMTP-Authentifizierungspasswort |
| `MAIL_DEFAULT_SENDER` | — | Absenderadresse für ausgehende E-Mails |
| `MAIL_SUPPRESS_SEND` | `true` | Auf `false` setzen, um echten E-Mail-Versand zu aktivieren |
| `GITHUB_CLIENT_ID` | — | GitHub-OAuth-App-Client-ID |
| `GITHUB_CLIENT_SECRET` | — | GitHub-OAuth-App-Client-Secret |
| `IMAP_HOST` | — | IMAP-Server für eingehende E-Mails |
| `IMAP_PORT` | `993` | IMAP-Port |
| `IMAP_USER` | — | IMAP-Anmeldebenutzername |
| `IMAP_PASSWORD` | — | IMAP-Anmeldepasswort |
| `IMAP_FOLDER` | `INBOX` | Abzurufendes Postfach |
| `IMAP_INTERVAL` | `60` | Abrufintervall in Sekunden |

Nach dem Ändern von Umgebungsvariablen muss die Anwendung neu gestartet werden.

---

## 8. Eingehende E-Mails

Bei entsprechender Konfiguration fragt Taskify ein IMAP-Postfach ab und leitet Antworten automatisch an das richtige Ticket weiter.

### Funktionsweise

1. Jede ausgehende E-Mail an einen Einreicher enthält eine Antwort-Adresse mit dem Ticket-Token.
2. Der Hintergrund-Thread fragt das IMAP-Postfach alle `IMAP_INTERVAL` Sekunden ab.
3. Passende Antworten werden als Kundennachricht an das Ticket angehängt.
4. Nicht passende E-Mails werden im Postfach belassen (nicht gelöscht).

### Einrichtung

1. Erstellen Sie ein dediziertes Postfach für Taskify (z. B. `support@example.com`).
2. Setzen Sie die IMAP-Umgebungsvariablen aus der obigen Tabelle.
3. Setzen Sie `MAIL_DEFAULT_SENDER` auf dieselbe Adresse, damit Antworten im richtigen Postfach ankommen.
4. Starten Sie die App neu. Die Systemtests zeigen, ob der IMAP-Thread läuft und sich authentifizieren kann.

### Thread-Zustand

Der Thread für eingehende E-Mails wird auf der Seite **Systemtests** unter *Eingehende E-Mails* angezeigt. Wenn er als *nicht aktiv* angezeigt wird, überprüfen Sie die IMAP-Zugangsdaten und starten Sie die App neu.

---

## 9. Notfallmaßnahmen

### Admin gesperrt / Passwort vergessen

Wenn sich kein Admin anmelden kann, setzen Sie ein Passwort direkt in der Datenbank zurück:

```bash
python - <<'EOF'
from app import app, db
from models import Employee
from werkzeug.security import generate_password_hash

with app.app_context():
    emp = Employee.query.filter_by(email='admin@example.com').first()
    emp.password_hash = generate_password_hash('NeuesPasswort1!')
    db.session.commit()
    print('Fertig')
EOF
```

Ersetzen Sie E-Mail und Passwort nach Bedarf.

### Kundenpasswort zurücksetzen

Öffnen Sie das Kundenbearbeitungsformular auf der Seite **Kunden** (Admin oder Manager), geben Sie ein neues Passwort ein und klicken Sie auf **Speichern**. Lassen Sie das Passwortfeld leer, um das aktuelle Passwort beizubehalten.

### Datenbank- und Anhänge-Backup

Verwenden Sie `backup.sh` im Projektstamm. Das Skript erstellt eine Hot-Backup-Kopie der Datenbank (kein Downtime erforderlich) und ein komprimiertes Archiv aller hochgeladenen Dateien. Ältere Backups werden automatisch gelöscht.

```bash
# Manuell ausführen
/opt/taskify/backup.sh -d /var/backups/taskify -v

# Als Cron-Job (täglich um 02:00)
0 2 * * * /opt/taskify/backup.sh -d /var/backups/taskify >> /var/log/taskify/backup.log 2>&1
```

Optionen: `-d DIR` Zielverzeichnis, `-k TAGE` Aufbewahrungsdauer (Standard: 14), `-v` ausführliche Ausgabe.

**Wiederherstellen:**

Führen Sie `restore.sh` ohne Argumente aus, um ein interaktives Auswahlmenü zu erhalten. Vor dem Überschreiben erstellt das Skript automatisch einen Sicherheits-Snapshot des aktuellen Zustands.

```bash
# Interaktiv — Backup aus nummerierter Liste auswählen
/opt/taskify/restore.sh -d /var/backups/taskify

# Neuestes Backup ohne Rückfragen wiederherstellen
/opt/taskify/restore.sh -d /var/backups/taskify -t latest -y

# Vorschau ohne Änderungen (Dry Run)
/opt/taskify/restore.sh -d /var/backups/taskify -t latest -n
```

### Anhänge bereinigen

Hochgeladene Dateien werden in `UPLOAD_FOLDER/<ticket_id>/` gespeichert. Beim Löschen eines Tickets über die Admin-Oberfläche wird der zugehörige Upload-Ordner automatisch entfernt. Um Speicherplatz durch anderweitig verwaiste Ordner freizugeben, löschen Sie diese manuell, nachdem Sie bestätigt haben, dass die entsprechenden Tickets nicht mehr existieren.
