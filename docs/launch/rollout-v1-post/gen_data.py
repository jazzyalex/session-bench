#!/usr/bin/env python3
"""Front-matter data for the Session-Bench v1 post: scores from data/leaderboard-v1.yml,
bytes and repeat counts from the metric rows of the packet replays (mean of three runs)."""
import json, yaml
SB = '/Users/alexm/Repository/session-bench'
rows = json.load(open('/private/tmp/claude-501/v1-metric-rows.json'))
lb = yaml.safe_load(open(SB + '/data/leaderboard-v1.yml'))
SHORT = {'deepseek-harness-cli': 'DeepSeek Harness', 'pi': 'Pi', 'copilot': 'Copilot CLI', 'opencode-cli': 'OpenCode', 'kimi': 'Kimi Code',
         'claude-cli': 'Claude Code', 'openclaw': 'OpenClaw', 'codex-cli': 'Codex', 'hermes': 'Hermes', 'claude-desktop': 'Claude Desktop',
         'antigravity': 'Antigravity', 'cursor-cli': 'Cursor CLI'}
CATS = [('record_fidelity', 'Fidelity', 30), ('causality_context', 'Causality', 20), ('usage_attribution', 'Usage', 15),
        ('portability_openness', 'Portability', 20), ('durability_signal', 'Durability', 15)]
def tot(r, m, k): return sum(x[m][k] for x in r)
def full(r, m): return all(x[m]['state'] == 'measured' and x[m]['correct'] == max(x[m]['decoded_eligible'], x[m]['observed_eligible']) for x in r)
def tone(p): return 'full' if p >= 99.5 else 'mid' if p >= 70 else 'low'
BUILD = {'pi': '1.0.0', 'openclaw': '2026.9.8', 'hermes': '0.21.5', 'cursor-cli': '2026.10.01'}
MODEL = {'opencode-cli': 'muse-spark-1.3', 'claude-cli': 'claude-sonnet-5', 'cursor-cli': 'cursor-grok-4.5'}
agents = []
for a in lb['agents']:
    r = rows[a['id']]
    den, dup = 'broad.classified_content_density', 'broad.naive_reader_duplicate_safety'
    total = sum(x[den]['decoded_eligible'] for x in r) / 3; useful = sum(x[den]['correct'] for x in r) / 3
    content = sum(100 * x[den]['correct'] / x[den]['decoded_eligible'] for x in r) / 3
    events, statements, once = tot(r, dup, 'observed_eligible'), tot(r, dup, 'decoded_eligible'), tot(r, dup, 'correct')
    cats = []
    for key, label, points in CATS:
        value = float(a['categories'][key]); pct = round(100 * value / points, 1)
        cats.append({'label': label, 'abbr': {'Causality': 'Causal', 'Portability': 'Portable', 'Durability': 'Durable'}.get(label, label), 'value': a['categories'][key].rstrip('0').rstrip('.') if '.' in a['categories'][key] else a['categories'][key],
                     'max': points, 'pct': pct, 'tone': tone(pct)})
    usage_full = full(r, 'attribution.usage'); tokens_full = full(r, 'attribution.token_semantics')
    stamps = round(100 * tot(r, 'broad.event_timestamps', 'correct') / tot(r, 'broad.event_timestamps', 'observed_eligible'))
    once_pct = round(100 * once / events)
    grid = [
        'full' if all(full(r, m) for m in ('work.submitted_turns', 'work.visible_responses', 'work.actions', 'work.results', 'work.changed_files', 'causal.action_result', 'causal.turn_response')) else 'part',
        'full' if once == events else 'part' if once else 'none',
        'full' if usage_full and tokens_full else 'part' if tot(r, 'attribution.usage', 'correct') else 'none',
        'full' if full(r, 'attribution.reconciliation') else 'none',
        'full' if full(r, 'broad.standard_tools_readable') else 'none',
        'full' if full(r, 'broad.declared_format_version') else 'none',
        'full' if stamps == 100 else 'part',
    ]
    agents.append({'rank': a['rank'], 'id': a['id'], 'name': SHORT[a['id']], 'score': a['score'], 'build': BUILD.get(a['id'], a['build']), 'model': MODEL.get(a['id'], a['model']),
                   'cats': cats, 'kb': round(total / 1000), 'session_kb': round(useful / 1000), 'content_pct': round(content),
                   'copies': f'{statements / events:.1f}', 'blocks': [100] * int(statements / events) + ([round(100 * (statements / events % 1))] if round(100 * (statements / events % 1)) >= 4 else []), 'once_pct': once_pct,
                   'events': events, 'statements': statements, 'once': once, 'stamps_pct': stamps, 'grid': grid})
top = max(a['kb'] for a in agents)
for a in agents:
    a['kb_pct'] = max(round(100 * a['kb'] / top, 1), 0.9)
doc = {'bench': {'agents': agents, 'by_size': [a['id'] for a in sorted(agents, key=lambda a: a['kb'])],
                 'by_copies': [a['id'] for a in sorted(agents, key=lambda a: (float(a['statements']) / a['events'], a['rank']))]}}
print(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000), end='')
