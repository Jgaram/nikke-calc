#!/usr/bin/env bash
# 서버 상태 한 눈에. `bash deploy/monitor.sh` 또는 `watch -n5 bash deploy/monitor.sh`
#
# pm2가 아니라 systemd로 돌기 때문에 `pm2 monit`이 없다. 대신 systemd가 세는 값과
# 커널 값을 그대로 읽어 온다.
set -u
U=nikke-decklab

echo "── 시스템 ────────────────────────────────────────────"
uptime | sed 's/^/  /'
free -h | awk 'NR<=2{print "  "$0}'
df -h / | awk 'NR==2{printf "  디스크 %s / %s (%s)\n", $3, $2, $5}'
printf "  CPU  "; top -bn2 -d0.5 | awk '/^%Cpu/{l=$0} END{print l}' | sed 's/%Cpu(s)://'

echo
echo "── 서비스 ────────────────────────────────────────────"
for u in "$U" naverreport; do
  systemctl is-active --quiet "$u" 2>/dev/null || { printf "  %-16s (없음/정지)\n" "$u"; continue; }
  read -r mem cpu tasks < <(systemctl show -p MemoryCurrent -p CPUUsageNSec -p TasksCurrent \
    --value "$u" | tr '\n' ' ')
  printf "  %-16s 메모리 %6.1f MB · 누적 CPU %8.1f 초 · 스레드 %s\n" \
    "$u" "$(echo "$mem/1048576" | bc -l)" "$(echo "$cpu/1000000000" | bc -l)" "$tasks"
done

echo
echo "── 앱 지표 (테일넷 전용 라우트) ──────────────────────"
curl -s --max-time 5 http://127.0.0.1:8766/api/stats   | python3 "$(dirname "$0")/monitor_stats.py"
