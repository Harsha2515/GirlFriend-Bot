# Deploying to Oracle Cloud Always Free

Running this bot 24/7 for ₹0, on a real VM with a real disk that survives
reboots and redeploys.

**Why this instead of Render free:** Render's free tier sleeps after 15 minutes
of inactivity and its disk is ephemeral — `bot.db` is wiped on every redeploy,
taking your history, facts, and pending reminders with it. Persistent disks are
paid-only there. An Oracle Always Free VM has a 50 GB boot volume that just
stays, and the process never sleeps.

**What it costs:** nothing, indefinitely. Oracle asks for a card at signup to
verify identity and it is not charged, as long as you stay on Always Free
shapes (see step 3).

---

## 1. Create the account

Go to [cloud.oracle.com](https://cloud.oracle.com) → **Start for free**.

⚠️ **Choose your Home Region carefully — it cannot be changed later.** For
India pick **India South (Hyderabad)** or **India West (Mumbai)**. Pick the one
closest to you; if one is persistently out of capacity in step 3, the other is
worth a fresh look.

You get a 30-day trial with credits *on top of* Always Free. When the trial
ends, Always Free resources keep running — you do **not** need to upgrade. If
you see a prompt to "Upgrade to Paid", ignore it.

---

## 2. Add an SSH key

On your Windows machine:

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\oracle_gfbot
```

Press Enter twice to skip the passphrase. This makes two files —
`oracle_gfbot` (private, keep it) and `oracle_gfbot.pub` (public, you'll paste
this next).

```powershell
Get-Content $env:USERPROFILE\.ssh\oracle_gfbot.pub
```

---

## 3. Create the VM

**Compute → Instances → Create instance**

| Setting | Value |
|---|---|
| Name | `gfbot` |
| Image | **Canonical Ubuntu 24.04** (22.04 is fine too) |
| Shape | See below |
| SSH keys | **Paste public keys** → paste the `.pub` contents from step 2 |

**Every option you pick must show the green `Always Free Eligible` badge.** If
it doesn't, you'll be billed. That badge is the only thing that matters here.

For the shape, click **Change shape** and choose one of:

- **`VM.Standard.A1.Flex`** (Ampere ARM) — set **1 OCPU / 6 GB RAM**. Roomy.
- **`VM.Standard.E2.1.Micro`** (AMD) — 1/8 OCPU, 1 GB RAM. Also fine; the setup
  script adds swap automatically.

> **"Out of host capacity" on the ARM shape?** This is the single most common
> Oracle frustration and it's not something you did wrong — A1 capacity in the
> Indian regions is heavily contested. Options, in order of ease:
> 1. Switch to **`VM.Standard.E2.1.Micro`**. It runs this bot perfectly well —
>    the workload is a polling loop and some API calls, not heavy compute.
> 2. Try a different **Availability Domain** in the dropdown.
> 3. Retry the A1 shape at off-peak hours. Capacity frees up irregularly.
>
> Don't wait days for ARM. Take the micro VM and move on.

Click **Create**, wait for the state to go green, and copy the **Public IP
address**.

---

## 4. Connect

```powershell
ssh -i $env:USERPROFILE\.ssh\oracle_gfbot ubuntu@<YOUR_PUBLIC_IP>
```

(The default user is `ubuntu` for Ubuntu images.)

---

## 5. Deploy

On the VM:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone <your-repo-url> ~/GirlFriend-Bot
cd ~/GirlFriend-Bot
bash deploy/setup.sh
```

The first run creates an empty `.env` and stops. Fill in your two keys:

```bash
nano ~/GirlFriend-Bot/.env
```

```env
TELEGRAM_BOT_TOKEN=<your BotFather token>
GEMINI_API_KEY=<your Gemini key>
DB_PATH=bot.db
TIMEZONE_DEFAULT=Asia/Kolkata
```

`Ctrl+O`, `Enter`, `Ctrl+X` to save. Then run it again:

```bash
bash deploy/setup.sh
```

This time it installs dependencies, runs `validate.py` as a pre-flight check,
installs the systemd service, and schedules nightly backups.

> **Private repo?** `git clone` will ask for credentials. Either use a GitHub
> [personal access token](https://github.com/settings/tokens) as the password,
> or skip git and copy the folder up from Windows:
> ```powershell
> scp -i $env:USERPROFILE\.ssh\oracle_gfbot -r "c:\Z - Work\GirlFriend-Bot" ubuntu@<IP>:~/
> ```
> Never commit `.env` — it's gitignored, and the setup script creates it on the
> server instead.

---

## 6. Confirm it's alive

```bash
journalctl -u gfbot -f
```

You want to see:

```
Initialising database...
SQLite DB ready at: /home/ubuntu/GirlFriend-Bot/bot.db
APScheduler started.
Restored reminders — 0 upcoming, 0 delivering late, 0 too stale.
Bot started in polling mode.
```

Message your bot on Telegram. Then prove the part that was broken on Render:

```bash
sudo systemctl restart gfbot
journalctl -u gfbot -n 20
```

`Restored reminders — N upcoming` means your pending nudges came back from
SQLite. That line is the whole point of this setup.

---

## No firewall configuration needed

The bot uses **long polling** — it makes outbound HTTPS calls to Telegram and
nothing connects *in*. So there's nothing to open in your Oracle security list,
and nothing to configure in `ufw` or `iptables`.

Leave `PORT` unset. The health check server in `main.py` only starts when
`PORT` exists, which is a Render-ism; on a VM you don't want it, and not
binding a port is one less thing exposed.

---

## Day-to-day

| Task | Command |
|---|---|
| Live logs | `journalctl -u gfbot -f` |
| Recent errors | `journalctl -u gfbot -p err -n 50` |
| Restart | `sudo systemctl restart gfbot` |
| Stop | `sudo systemctl stop gfbot` |
| Is it running? | `systemctl is-active gfbot` |
| Deploy new code | `cd ~/GirlFriend-Bot && git pull && bash deploy/setup.sh` |
| Back up now | `~/GirlFriend-Bot/deploy/backup.sh` |

`setup.sh` is safe to re-run — that's the intended update path.

---

## Backups

`deploy/backup.sh` runs nightly at 3 AM via cron, keeping 14 compressed
snapshots in `~/GirlFriend-Bot/backups/`. It uses sqlite3's `.backup` rather
than `cp`, because the bot runs in WAL mode and a plain copy can capture a torn
database.

**Restore:**

```bash
sudo systemctl stop gfbot
cd ~/GirlFriend-Bot
gunzip -c backups/bot-20260910-030000.db.gz > bot.db
sudo systemctl start gfbot
```

**Pull a copy down to Windows** (worth doing occasionally — a backup on the
same disk doesn't protect against losing the VM):

```powershell
scp -i $env:USERPROFILE\.ssh\oracle_gfbot "ubuntu@<IP>:~/GirlFriend-Bot/backups/*.gz" .
```

---

## Keeping the VM

Two things reclaim Always Free instances. Both are avoidable:

1. **Idle reclamation.** Oracle may reclaim Always Free compute that sits idle
   for 7 days. A bot polling Telegram continuously generates steady network and
   CPU activity, so this shouldn't trigger — but it's a reason not to leave the
   service stopped for a week.
2. **Account inactivity.** Log into the Oracle console occasionally.

If the VM does get reclaimed, your restore path is the backup you copied to
Windows. That's the whole reason to keep one off the box.

---

## Troubleshooting

**Service won't start.** `journalctl -u gfbot -n 50` shows the traceback. Most
often it's a missing key — `config.py` raises a clear error naming which one.

**Crash-looping.** The unit backs off after 5 restarts in 5 minutes and stays
down deliberately, so you get a readable log instead of a flood. Fix the cause,
then `sudo systemctl start gfbot`.

**Reminders at the wrong hour.** The VM runs UTC; that's correct and expected.
All reminder times are stored in UTC and converted using *your* timezone from
the database, not the server's clock. Check yours with `/timezone` in Telegram.
Don't change the server timezone.

**`python3 -m venv` fails.** `sudo apt-get install -y python3-venv`, then re-run
`setup.sh`.

**Out of memory on the micro VM.** `free -h` should show a 2 GB swapfile added
by `setup.sh`. If it's missing, re-run the script.
