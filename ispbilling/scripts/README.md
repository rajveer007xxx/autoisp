# Auto-Renewal System Scripts

This directory contains scripts and systemd configuration for the admin auto-renewal system.

## Files

- `auto_renew_admins.py` - Main auto-renewal script that processes expired admins
- `autoispbilling-auto-renew.service` - Systemd service configuration
- `autoispbilling-auto-renew.timer` - Systemd timer configuration (runs daily at 2:00 AM IST)

## Installation

To install the auto-renewal system on the VPS:

```bash
# 1. Copy systemd files to system directory
sudo cp /home/ubuntu/autoispbilling-payfast-repo/scripts/autoispbilling-auto-renew.service /etc/systemd/system/
sudo cp /home/ubuntu/autoispbilling-payfast-repo/scripts/autoispbilling-auto-renew.timer /etc/systemd/system/

# 2. Create log directory
sudo mkdir -p /var/log/autoispbilling
sudo chown root:root /var/log/autoispbilling

# 3. Make script executable
sudo chmod +x /home/ubuntu/autoispbilling-payfast-repo/scripts/auto_renew_admins.py

# 4. Reload systemd daemon
sudo systemctl daemon-reload

# 5. Enable and start the timer
sudo systemctl enable autoispbilling-auto-renew.timer
sudo systemctl start autoispbilling-auto-renew.timer

# 6. Check timer status
sudo systemctl status autoispbilling-auto-renew.timer
sudo systemctl list-timers --all | grep autoispbilling
```

## Manual Testing

To manually run the auto-renewal script for testing:

```bash
# Run the script directly
sudo python3 /home/ubuntu/autoispbilling-payfast-repo/scripts/auto_renew_admins.py

# Or trigger the service manually
sudo systemctl start autoispbilling-auto-renew.service

# Check logs
sudo tail -f /var/log/autoispbilling/auto-renew.log
sudo tail -f /var/log/autoispbilling/auto-renew-error.log
```

## How It Works

1. **Daily Schedule**: The timer runs every day at 2:00 AM IST (Asia/Kolkata timezone)

2. **Admin Selection**: The script queries all admins where:
   - `admin_type != "Trial"` (skip trial admins)
   - `end_date <= today` (expired or expiring today)
   - `auto_renew_enabled == 1` (auto-renewal is enabled)

3. **Renewal Process**: For each expired admin:
   - Determines renewal period from `period_months` field (defaults to 1 month)
   - Calculates renewal amount based on package price
   - Generates invoice PDF
   - Sends invoice via email
   - Updates `balance_amount` (adds renewal amount)
   - Extends `end_date` by renewal period
   - Sets `status` to "Deactivated" (until payment is received)
   - Logs renewal in `renewal_logs` table (prevents duplicate renewals)

4. **Idempotency**: The `renewal_logs` table with unique constraint on `(company_id, period_start, period_end, method)` ensures that the same renewal is not processed twice, even if the script runs multiple times.

5. **Status Management**:
   - After auto-renewal: Status set to "Deactivated"
   - After payment recorded: Status automatically set to "Active" if balance <= 0
   - Manual override: Superadmin can manually set status to "Active" regardless of balance

## Logs

- **Success Log**: `/var/log/autoispbilling/auto-renew.log`
- **Error Log**: `/var/log/autoispbilling/auto-renew-error.log`

## Troubleshooting

### Timer not running
```bash
# Check if timer is enabled
sudo systemctl is-enabled autoispbilling-auto-renew.timer

# Check timer status
sudo systemctl status autoispbilling-auto-renew.timer

# View timer schedule
sudo systemctl list-timers --all | grep autoispbilling
```

### Script errors
```bash
# Check error logs
sudo tail -100 /var/log/autoispbilling/auto-renew-error.log

# Test script manually
sudo python3 /home/ubuntu/autoispbilling-payfast-repo/scripts/auto_renew_admins.py
```

### Timezone issues
The service is configured with `TZ=Asia/Kolkata` to ensure it runs at 2:00 AM IST. Verify with:
```bash
# Check current timezone
timedatectl

# Verify timer next run time
sudo systemctl list-timers --all | grep autoispbilling
```

## Disabling Auto-Renewal

To disable auto-renewal for a specific admin:
```sql
UPDATE companies SET auto_renew_enabled = 0 WHERE company_id = 'COMPANY_ID';
```

To disable the entire auto-renewal system:
```bash
sudo systemctl stop autoispbilling-auto-renew.timer
sudo systemctl disable autoispbilling-auto-renew.timer
```
