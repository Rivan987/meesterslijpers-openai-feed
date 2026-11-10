#!/usr/bin/env python3
"""Upload dist/<OUT_NAME>.csv.gz naar de SFTP-root van de OpenAI Ads-feed.

Vereiste omgevingsvariabelen (als GitHub Secrets):
  OPENAI_SFTP_HOST, OPENAI_SFTP_USER, OPENAI_SFTP_PASSWORD
Optioneel: OPENAI_SFTP_PORT (standaard 22), OUT_NAME (standaard meesterslijpers_openai)
"""
import os
import sys

import paramiko

host = os.environ.get("OPENAI_SFTP_HOST")
if not host:
    print("Geen SFTP-gegevens ingesteld, upload overgeslagen.")
    sys.exit(0)

name = os.environ.get("OUT_NAME", "meesterslijpers_openai") + ".csv.gz"
local = os.path.join(os.environ.get("OUT_DIR", "dist"), name)
transport = paramiko.Transport((host, int(os.environ.get("OPENAI_SFTP_PORT") or "22")))
transport.connect(username=os.environ["OPENAI_SFTP_USER"], password=os.environ["OPENAI_SFTP_PASSWORD"])
sftp = paramiko.SFTPClient.from_transport(transport)
# Altijd dezelfde bestandsnaam in de root, overschrijven = volledige snapshot.
sftp.put(local, name)
sftp.close()
transport.close()
print("Upload naar SFTP gelukt.")
