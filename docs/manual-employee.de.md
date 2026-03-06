# Taskify – Mitarbeiter-Handbuch

## Inhaltsverzeichnis

1. [Anmelden](#1-anmelden)
2. [Das Dashboard](#2-das-dashboard)
3. [Ein Ticket öffnen](#3-ein-ticket-offnen)
4. [Antworten und Notizen](#4-antworten-und-notizen)
5. [Status ändern](#5-status-andern)
6. [Tickets zuweisen](#6-tickets-zuweisen)
7. [Tickets beobachten](#7-tickets-beobachten)
8. [Suche](#8-suche)
9. [E-Mail-Benachrichtigungen](#9-e-mail-benachrichtigungen)
10. [Sprache](#10-sprache)
11. [Projektzuweisung](#11-projektzuweisung)

---

## 1. Anmelden

Gehen Sie zu `/login` und geben Sie Ihre E-Mail-Adresse und Ihr Passwort ein.

Wenn Ihr Konto mit einem GitHub-Profil verknüpft ist, können Sie auch auf **Mit GitHub anmelden** klicken.

---

## 2. Das Dashboard

Das Dashboard zeigt alle Tickets im System.

### Ansichten

| Reiter | Zeigt |
|--------|-------|
| **Alle** | Jedes Ticket |
| **Meine** | Ihnen zugewiesene Tickets |
| **Beobachtet** | Von Ihnen beobachtete Tickets |

Das Badge auf jedem Reiter zeigt die Anzahl der aktiven Tickets.

### Zusammenfassungskarten

Die vier Karten oben zählen Tickets in der aktuellen Ansicht. Klicken Sie auf eine Karte, um sofort nach diesem Status zu filtern.

| Karte | Bedeutung |
|-------|-----------|
| Offen | Noch nicht bearbeitet |
| In Bearbeitung | Wird aktiv behandelt |
| Nicht zugewiesen | Noch kein Bearbeiter (im Reiter *Meine* ausgeblendet) |
| Diese Woche gelöst | In den letzten 7 Tagen gelöst oder geschlossen |

### Filter

- **Suchfeld** — durchsucht Betreff, Beschreibung, Nachrichten und Absender-E-Mail.
- **Status-Dropdown** — zeigt nur einen Status gleichzeitig.
- **Projekt-Dropdown** — filtert nach Projekt (erscheint wenn Projekte vorhanden).
- **Zurücksetzen** — entfernt alle aktiven Filter.

Geschlossene Tickets sind standardmäßig ausgeblendet. Klicken Sie auf *Geschlossene ausgeblendet*, um sie anzuzeigen.

### Badge „Antwort ausstehend"

Ein gelbes Badge in einer Ticketzeile bedeutet, dass der Kunde geantwortet hat und auf eine Rückmeldung wartet.

---

## 3. Ein Ticket öffnen

Klicken Sie auf den Ticketbetreff oder die ID, um die Detailansicht zu öffnen.

**Farbkodierung der Nachrichten:**

| Rahmenfarbe | Bedeutung |
|-------------|-----------|
| Orange | Kundennachricht |
| Grün | Mitarbeiterantwort (für den Kunden sichtbar) |
| Grau | Interne Notiz — nur für Mitarbeiter |

Die **Seitenleiste** rechts enthält alle Aktionen: Status, Bearbeiter, Beobachten, interner Titel, Projektzuweisung und Ticketinfo.

Das **Aktivitätsprotokoll** am unteren Ende der Seitenleiste erfasst jeden Statuswechsel, jede Zuweisung und jeden Datei-Upload.

---

## 4. Antworten und Notizen

1. Schreiben Sie im Editor am unteren Ende des Tickets.
2. Hängen Sie optional eine Datei über das Upload-Feld an.
3. Setzen Sie den Haken bei **Für den Kunden sichtbar (sendet E-Mail)**, um die Nachricht an den Einreicher zu senden.
   Lassen Sie den Haken weg, um eine interne Notiz zu schreiben, die nur Mitarbeiter sehen.
4. Klicken Sie auf **Senden**.

Eigene Nachrichten können Sie nachträglich mit dem Stift-Symbol bearbeiten.

---

## 5. Status ändern

Verwenden Sie das Panel *Status ändern* in der Seitenleiste.

| Status | Bedeutung |
|--------|-----------|
| Offen | Eingegangen – noch nicht in Bearbeitung |
| In Bearbeitung | Wird aktiv behandelt |
| Gelöst | Behoben – wartet auf Bestätigung des Einreichers |
| Geschlossen | Vollständig abgeschlossen, keine weiteren Maßnahmen erwartet |

Jede Statusänderung sendet eine Benachrichtigungs-E-Mail an den Einreicher.

---

## 6. Tickets zuweisen

Verwenden Sie das Panel *Bearbeiter* in der Seitenleiste. Wählen Sie einen Mitarbeiter und klicken Sie auf **Zuweisen**. Der zugewiesene Mitarbeiter erhält eine E-Mail-Benachrichtigung. Wählen Sie *Nicht zugewiesen*, um den aktuellen Bearbeiter zu entfernen.

---

## 7. Tickets beobachten

Klicken Sie in der Seitenleiste auf **Beobachten**, um E-Mail-Updates für Tickets zu erhalten, die Ihnen nicht zugewiesen sind. Klicken Sie auf **Nicht mehr beobachten**, um diese zu stoppen. Die Schaltfläche wird ausgeblendet, wenn Sie bereits der Bearbeiter sind, da Sie automatisch alle Benachrichtigungen erhalten.

---

## 8. Suche

Klicken Sie auf das Lupensymbol in der Navigationsleiste oder gehen Sie zu `/search`.

Sie können nach beliebigen Kombinationen filtern: Stichwort, Status, Bearbeiter, Datumsbereich und Projekt. Ergebnisse werden seitenweise angezeigt, und die URL spiegelt alle aktiven Filter wider – speichern oder teilen Sie sie beliebig.

---

## 9. E-Mail-Benachrichtigungen

| Ereignis | Wer erhält eine E-Mail |
|----------|------------------------|
| Ticket Ihnen zugewiesen | Sie |
| Kunde antwortet | Bearbeiter (oder alle aktiven Mitarbeiter, wenn nicht zugewiesen) |
| Beobachtetes Ticket aktualisiert | Sie |
| Status geändert / Nachricht an Kunden gesendet | Einreicher |

---

## 10. Sprache

Klicken Sie auf die Sprachschaltfläche in der oberen Navigationsleiste, um zwischen **Englisch** und **Deutsch** zu wechseln. Die Einstellung wird pro Browser-Sitzung gespeichert.

---

## 11. Projektzuweisung

Jedes Ticket kann über die Seitenleiste einem **Projekt** zugewiesen werden. Alle Kunden, die Mitglieder dieses Projekts sind, sehen das Ticket dann in ihrem Tab **Projekttickets**.

Wählen Sie ein Projekt aus dem Dropdown *Projekt* in der Seitenleiste und klicken Sie auf **Speichern**. Wählen Sie *— Kein Projekt —*, um die Zuweisung zu entfernen.

Die Seitenleiste zeigt „Sichtbar für alle Kunden in **Projektname**", wenn ein Projekt gesetzt ist.
