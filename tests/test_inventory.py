import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from read_inventory import Reader, build_inventory, protobuf, text_vdf


class InventoryTests(unittest.TestCase):
    def test_uninstalled_and_duplicate_family_entitlements(self):
        grants = [{'package_id': 10, 'owner_id': 42, 'flags': 0},
                  {'package_id': 11, 'owner_id': 99, 'flags': 0x4000},
                  {'package_id': 12, 'owner_id': 42, 'flags': 8}]
        packages = {10: {1, 2}, 11: {2, 3, 4, 5}, 12: {6}}
        apps = {key: {'common': {'name': str(key), 'type': kind}} for key, kind in
                [(1, 'Game'), (2, 'Game'), (3, 'Game'), (4, 'DLC'), (6, 'Game')]}
        apps[5] = {'common': {'name': 'Excluded', 'type': 'Game', 'exfgls': '1'}}
        rows, stats = build_inventory(grants, packages, apps, {1}, 42, True)
        by_id = {row['appid']: row for row in rows}
        self.assertEqual(set(by_id), {1, 2, 3, 4, 5})
        self.assertEqual(by_id[2]['ownership'], 'own')
        self.assertFalse(by_id[2]['installed'])
        self.assertEqual(by_id[3]['ownership'], 'family')
        self.assertEqual(by_id[4]['decision'], 'other_content')
        self.assertEqual(by_id[5]['decision'], 'family_excluded')
        self.assertEqual(stats['inactive_license_count'], 1)
        own, _ = build_inventory(grants, packages, apps, set(), 42, False)
        self.assertEqual({row['appid'] for row in own}, {1, 2})

    def test_missing_mapping_and_unknown_type_are_visible(self):
        grants = [{'package_id': 10, 'owner_id': 42, 'flags': 0},
                  {'package_id': 11, 'owner_id': 42, 'flags': 0}]
        rows, stats = build_inventory(grants, {10: {7}}, {}, set(), 42, True)
        self.assertEqual(stats['missing_package_ids'], [11])
        self.assertEqual(stats['missing_app_ids'], [7])
        self.assertEqual(rows[0]['decision'], 'pending_type')

    def test_wire_format_handles_unknown_fields(self):
        payload = b'\x08\x01\x12\x02\x08\x7b\x1d' + struct.pack('<I', 456)
        self.assertEqual(protobuf(payload), [(1, 1), (2, b'\x08\x7b'), (3, 456)])
        with self.assertRaises(ValueError):
            protobuf(b'\x12\x05x')

    def test_text_vdf_paths_and_chinese(self):
        obj = text_vdf('"root" { "path" "fixture\\\\games\\\\steam" "name" "中文" }')
        self.assertEqual(obj['root']['path'], 'fixture\\games\\steam')
        self.assertEqual(obj['root']['name'], '中文')

    def test_binary_vdf_keeps_nested_metadata(self):
        data = b'\x00common\0\x01name\0' + '游戏'.encode() + b'\0\x02appid\0' + struct.pack('<i', 123) + b'\x08\x08'
        self.assertEqual(Reader(data).kv(), {'common': {'name': '游戏', 'appid': 123}})
        with self.assertRaises(ValueError):
            Reader(data[:-1]).kv()


if __name__ == '__main__':
    unittest.main()