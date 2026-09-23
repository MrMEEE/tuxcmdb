import unittest

from tuxcmdb.hypervisors.base import extract_stats
from tuxcmdb.hypervisors.backends.xenserver import _extract_xmlrpc_result


class ExtractStatsTests(unittest.TestCase):
    def test_extract_stats_from_json_string(self) -> None:
        payload = '{"vm_count": 4, "cluster_cpus": 8, "cluster_memory_gb": 64, "vms": ["vm-a", "vm-b"]}'

        stats = extract_stats(payload)

        self.assertEqual(stats["vm_count"], 4)
        self.assertEqual(stats["cluster_cpus"], 8)
        self.assertEqual(stats["cluster_memory_gb"], 64)
        self.assertEqual(stats["vms"], ["vm-a", "vm-b"])

    def test_extract_stats_from_xml_string(self) -> None:
        payload = "<response><vm_count>3</vm_count><cpu_count>6</cpu_count><memory_gb>24</memory_gb><vms><vm>vm-1</vm><vm>vm-2</vm></vms></response>"

        stats = extract_stats(payload)

        self.assertEqual(stats["vm_count"], 3)
        self.assertEqual(stats["cluster_cpus"], 6)
        self.assertEqual(stats["cluster_memory_gb"], 24)
        self.assertEqual(stats["vms"], ["vm-1", "vm-2"])

    def test_extract_stats_from_xenapi_xmlrpc_response(self) -> None:
        payload = """<?xml version=\"1.0\"?>
<methodResponse>
  <params>
    <param>
      <value>
        <struct>
          <member>
            <name>cpu_info</name>
            <value>
              <struct>
                <member><name>cpu_count</name><value>6</value></member>
              </struct>
            </value>
          </member>
          <member>
            <name>memory_total</name>
            <value>34126819328</value>
          </member>
          <member>
            <name>resident_VMs</name>
            <value>
              <array>
                <data>
                  <value>OpaqueRef:vm-a</value>
                  <value>OpaqueRef:vm-b</value>
                </data>
              </array>
            </value>
          </member>
        </struct>
      </value>
    </param>
  </params>
</methodResponse>"""

        stats = extract_stats(payload)

        self.assertEqual(stats["vm_count"], 2)
        self.assertEqual(stats["cluster_cpus"], 6)
        self.assertEqual(stats["cluster_memory_gb"], 31)
        self.assertEqual(stats["vms"], ["OpaqueRef:vm-a", "OpaqueRef:vm-b"])

    def test_extract_xmlrpc_value_from_method_response(self) -> None:
        payload = {
            "params": {
                "param": {
                    "value": {
                        "Status": "Success",
                        "Value": "OpaqueRef:abc123",
                    }
                }
            }
        }

        self.assertEqual(_extract_xmlrpc_result(payload), "OpaqueRef:abc123")


if __name__ == "__main__":
    unittest.main()
