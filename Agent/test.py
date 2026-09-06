from datetime import datetime, timezone

end_time_str = datetime.now(timezone.utc).isoformat()

print(end_time_str)

now = datetime.now().isoformat() + "Z"

t_end = datetime.fromisoformat(end_time_str)


timestamp = datetime.now(timezone.utc).isoformat()

print(t_end)
print(timestamp)