# CANSAT NOVAsync

Deze repository bevat alle code voor ons CANSAT-project.\
Hier vind je alle modules, scripts en documentatie die gebruikt worden
voor de ontwikkeling, testen en integratie van onze CANSAT-systemen.

## Inhoud

-   **/src** -- Hoofdcode voor vluchtsoftware, sensoren en communicatie\
-   **/hardware** -- Schema's, pinouts en gerelateerde
    hardware-documentatie\
-   **/data** -- Testdata, logbestanden en ruwe meetresultaten\
-   **/docs** -- Technische documentatie, protocollen en ontwerpnotities

## Doel

Het doel van dit project is het bouwen, programmeren en valideren van
een werkende CANSAT die tijdens een vlucht telemetrie verzamelt, opslaat
en verzendt. Deze repository centraliseert alle projectbestanden om
samenwerking en versiebeheer te ondersteunen.

## Installatie

1.  Clone de repository

    ``` bash
    git clone https://github.com/<org>/CANSAT-NOVAsync.git
    ```

2.  Installeer vereiste dependencies (zie `/docs/dependencies.md` indien
    aanwezig).

## Gebruik

-   Ontwikkel en test code in de `/src` map.\
-   Documenteer wijzigingen in de relevante `/docs` bestanden.\
-   Commit en push regelmatig om teamleden up-to-date te houden.\
-   Voor automatische tijdssynchronisatie kun je het hostscripts `tools/pico_time_sync.py` gebruiken. Installeer eerst `pyserial` (`pip install pyserial`) en start vervolgens
    ```bash
    python tools/pico_time_sync.py COM5
    ```
    waarbij `COM5` vervangen wordt door de seriële poort van jouw Pico. Het script wacht op de `TIME_SYNC`-prompt van `main.py` en stuurt automatisch de UNIX-tijd.
-   Wil je dit rechtstreeks in Thonny doen? Kopieer `tools/thonny_plugin/pico_time_sync_plugin.py` naar jouw lokale Thonny plug-in map (`%APPDATA%\Thonny\plugins` op Windows). Na het herstarten van Thonny wordt bij elke `TIME_SYNC`-prompt automatisch de huidige tijd ingestuurd.

## Contributies

Nieuwe functies, verbeteringen en bugfixes zijn welkom. Maak bij grotere
wijzigingen eerst een issue of draft pull request aan.

## Licentie

Zie het bestand `LICENSE` voor licentie-informatie.
