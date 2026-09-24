"""Export an experimental physical layout; logical graph IDs never change."""
import argparse
import array
import configparser
import hashlib
import json
import mmap
import pathlib
import struct
import sys
from collections import deque

PAGE = 4096

def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()

def offset(slot, size):
    if size > PAGE:
        return slot * ((size + PAGE - 1) // PAGE) * PAGE
    return slot // (PAGE // size) * PAGE + slot % (PAGE // size) * size

def write_records(path, records, size):
    with path.open('xb') as stream:
        page = bytearray(PAGE)
        used = 0
        for record in records:
            if len(record) != size:
                raise ValueError('record length mismatch')
            if size > PAGE:
                stream.write(record)
                stream.write(bytes((-size) % PAGE))
                continue
            if used + size > PAGE:
                stream.write(page)
                page = bytearray(PAGE)
                used = 0
            page[used:used + size] = record
            used += size
        if used:
            stream.write(page)

def export(source, output):
    config = configparser.ConfigParser()
    config.read_string('[index]\n' + (source / 'index.meta').read_text())
    meta = config['index']
    n = int(meta['ours_record_count'])
    graph_size = int(meta['record_bytes'])
    compact = int(meta['ours_compact_record_bytes'])
    residual = int(meta['ours_residual_record_bytes'])
    original_size = compact + residual
    if min(n, graph_size, compact, residual) <= 0:
        raise ValueError('invalid source dimensions')
    output.mkdir(parents=True, exist_ok=False)
    inputs = [source / name for name in ['index.meta', 'shared_graph.pages', 'ours_full4_residual.pages', 'ours_quantizer.bin', 'ours_db1_sidecar.bin']]
    hashes = {str(p): digest(p) for p in inputs}
    with inputs[1].open('rb') as gf, inputs[2].open('rb') as pf:
        with mmap.mmap(gf.fileno(), 0, access=mmap.ACCESS_READ) as graph, mmap.mmap(pf.fileno(), 0, access=mmap.ACCESS_READ) as payload:
            def graph_record(node):
                start = offset(node, graph_size)
                return graph[start:start + graph_size]
            def payload_record(node):
                start = offset(node, original_size)
                return payload[start:start + original_size]
            seen = bytearray(n)
            order = array.array('I')
            pending = deque()
            # Start at the same logical entry point as the Ours query loop.
            for seed in range(n):
                if seen[seed]:
                    continue
                seen[seed] = 1
                pending.append(seed)
                while pending:
                    node = pending.popleft()
                    order.append(node)
                    row = graph_record(node)
                    degree, = struct.unpack_from('<I', row)
                    if degree > int(meta['max_degree']) or 4 + degree * 4 > len(row):
                        raise ValueError('invalid graph degree')
                    for (neighbor,) in struct.iter_unpack('<I', row[4:4 + degree * 4]):
                        if neighbor < n and not seen[neighbor]:
                            seen[neighbor] = 1
                            pending.append(neighbor)
            inverse = array.array('I', [0]) * n
            for slot, node in enumerate(order):
                inverse[node] = slot
            for name, data in [('slot_to_id.u32', order), ('id_to_slot.u32', inverse)]:
                encoded = array.array('I', data)
                if encoded.itemsize != 4:
                    raise ValueError('requires 32-bit unsigned integers')
                if sys.byteorder != 'little':
                    encoded.byteswap()
                with (output / name).open('xb') as stream:
                    encoded.tofile(stream)
            combined_size = graph_size + compact
            write_records(output / 'graph_compact.pages',
                          (graph_record(i) + payload_record(i)[:compact] for i in order), combined_size)
            write_records(output / 'residual.pages',
                          (payload_record(i)[compact:] for i in order), residual)
            with (output / 'graph_compact.pages').open('rb') as cf, (output / 'residual.pages').open('rb') as rf:
                with mmap.mmap(cf.fileno(), 0, access=mmap.ACCESS_READ) as combined, mmap.mmap(rf.fileno(), 0, access=mmap.ACCESS_READ) as rest:
                    for node in range(n):
                        slot = inverse[node]
                        a, b = offset(slot, combined_size), offset(slot, residual)
                        if combined[a:a + graph_size] != graph_record(node):
                            raise ValueError('adjacency verification failed')
                        if combined[a + graph_size:a + combined_size] + rest[b:b + residual] != payload_record(node):
                            raise ValueError('payload verification failed')
    if any(digest(p) != hashes[str(p)] for p in inputs):
        raise ValueError('source changed during export')
    manifest = dict(schema_version=1, experimental=True, formal_ready=False,
                    runtime_supported=False, source_hashes=hashes, record_count=n,
                    graph_bytes=graph_size, compact_bytes=compact, residual_bytes=residual,
                    layout='bfs_graph_compact_separate_residual', logical_ids_preserved=True,
                    resident_mapping_bytes=n * 4, all_records_verified=True,
                    auxiliary_graph_rows='retained only in source; query ignores IDs >= base count',
                    files={p.name: dict(bytes=p.stat().st_size, sha256=digest(p)) for p in output.iterdir()})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=pathlib.Path, required=True)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    export(args.source.resolve(), args.output.resolve())
