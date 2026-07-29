# Real SWE-bench Pro task selection

Dataset: `ScaleAI/SWE-bench_Pro`

Immutable revision: `7ab5114912baf22bb098818e604c02fe7ad2c11f`

The complete frozen test split contains `731` records. Metadata for all records
was screened without changing revision. Three new candidates were seriously
evaluated by inspecting each exact commit tree and official image manifest.
The previously imported OpenLibrary record remains separate blocker evidence.

| Row | Instance | Repository | Base commit | Tree SHA | Gitlinks | F2P / P2P | Patches | Official image | Environment | Result |
| ---: | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- |
| 407 | `instance_ansible__ansible-d9f1866249756efc264b00ff7497e92c11a9885f-v0f01c69f1e2528b935359cfe578530722bca2c59` | `https://github.com/ansible/ansible.git` | `59ca05b70994b07a9507f61a0871146a4991b262` | `64a85753dada2a0a05dcf13093dabbdae13cc7de` | 0 | 1 / 1 | golden: yes; test: yes | available; selected digest `sha256:f9e1f9d428d55a8f26b27d89f29819b79a82b847fd252903c68221b2812ccd04` | Python 3.11, pytest, one unit-test file, no services | **selected** |
| 95 | `instance_ansible__ansible-12734fa21c08a0ce8c84e533abdc560db2eb1955-v7eee2454f617569fd6889f2211f75bc02a35f9f8` | `https://github.com/ansible/ansible.git` | `de01db08d00c8d2438e1ba5989c313ba16a145b0` | `fb57551a10cf0efc643d5cecb59df4c730f5bad2` | 0 | 1 / 4 | golden: yes; test: yes | available; manifest inspected | Python 3.11, pytest, one unit-test file | not selected: five selectors versus two |
| 674 | `instance_qutebrowser__qutebrowser-5e0d6dc1483cb3336ea0e3dcbd4fe4aa00fc1742-v5149fcda2a9a6fe1d35dfed1bade1444a11ef271` | `https://github.com/qutebrowser/qutebrowser.git` | `4df7aedf2b2e1f844e8f0ba55bb6fdcafcfa8ec8` | `6809be279358018dc5d23ec3d9d0f6e966631807` | 0 | 1 / 5 | golden: yes; test: yes | available; manifest inspected | Python/pytest plus Qt and JavaScript runtime | not selected: avoid unnecessary Qt/JS complexity |

The selected Ansible record has a public HTTPS repository, a full commit, no
mode `160000` entries, no submodule requirement, complete public text, exact
golden and hidden patches, both selector groups, a public official image, no
runtime service or network dependency, and GPL-3.0 source suitable for this
private evaluation.

The official-image check with the exact hidden patch produced one passing P2P
and one failing F2P selector. Adding the exact golden patch produced two
passes. The CLI lifecycle independently reproduced those semantics.

OpenLibrary row `665` remains imported and provenance-verified. Its CLI source
import is still recorded as blocked by the two unsupported Gitlinks
`vendor/infogami` and `vendor/js/wmd`; it is not the successful demonstration
and no submodule workaround was added.
