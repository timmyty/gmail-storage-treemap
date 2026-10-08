"""App-level smoke test of our own widgets and filtering, without desktop capture."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gmail_storage_treemap import StorageTreemap
from gmail_storage_core import group_key
import threading
import time

app = StorageTreemap(demo=True)
app.withdraw()
app.update_idletasks()
assert len(app.current_groups) == 8
assert len(app.current_messages) == 2695
assert len(app.messages_tree.get_children()) == 150
assert 'DEMO' in app.stats.get()
app.change_page(1)
assert app.page == 1
assert app.page_text.get().startswith('151')
app.query.set('photos@example.com')
app.reset_focus()
assert len(app.current_messages) == 135
app.minimum.set('10 MiB+')
app.reset_focus()
assert all(m['size'] >= 10 * 1024 * 1024 for m in app.current_messages)
app.minimum.set('Any size')
app.query.set('')
app.mode.set('Year')
app.reset_focus()
assert len(app.current_groups) == 11
app.tree.selection_set('0')
app.select_group()
assert app.selected_group == app.current_groups[0]['name']
assert all(group_key(m, 'Year') == app.selected_group for m in app.current_messages)
app.reset_focus()
assert len(app.current_messages) == 2695
assert app.resume_button['state'] == 'disabled' or str(app.resume_button['state']) == 'disabled'
done = threading.Event()
app.start_work(lambda: (done.wait(5), app.snapshot)[1])
app.scan_dialog.withdraw()
app.update_scan_progress({'phase': 'listing', 'title': 'Listing messages', 'listed': 500})
assert '500' in app.scan_counts.get()
assert str(app.scan_bar['mode']) == 'indeterminate'
app.update_scan_progress({'phase': 'scanning', 'title': 'Reading message metadata', 'completed': 25, 'total': 100,
                          'bytes': 1048576, 'failed': 0, 'reused': 5})
assert app.scan_bar['value'] == 25
assert '25.0%' in app.scan_counts.get()
assert '5 reused' in app.scan_counts.get()
app.report('Gmail quota pause — retrying in 65 seconds. Results are kept.')
app.poll()
assert app.scan_phase.get() == 'Waiting for Gmail quota'
assert '65 seconds' in app.scan_detail.get()
app.stop_scan()
assert app.stop.is_set()
done.set()
deadline = time.monotonic() + 2
while app.busy and time.monotonic() < deadline:
    app.update()
    time.sleep(0.01)
assert not app.busy
assert str(app.dialog_stop['state']) == 'disabled'
assert app.dialog_hide['text'] == 'Close'
app.set_snapshot({'version': 1, 'source': 'Gmail size estimates', 'account': 'example@example.com', 'complete': False, 'messages': []})
assert str(app.resume_button['state']) == 'normal'
app.destroy()
print('App checks passed: grouping, drill-down, filters, progress phases/counts, quota pause, stop, finish and resume availability.')
