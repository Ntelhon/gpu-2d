# Security policy

## Supported versions

Security fixes are made on `main` and included in the next release. Only the latest release is supported.

| Version | Supported |
|---|---|
| 2.1 (latest) | Yes |
| < 2.1 | No |

## Scope

Relevant reports include, for example:

- hardware behaviour that lets software controlling G2D read or write memory outside the buffers it programmed
  (address checks, command validation, error handling in `spec/rendering.md` §13 and `spec/arch.md`);
- memory-safety or privilege issues in the Linux kernel driver (`sw/linux/`) or its user-space interface;
- memory-safety issues in the VGLite driver or HALs (`sw/driver/`, `sw/hal/`).

G2D checks every draw's buffers against an address window (`ADDR_WIN_LO` / `ADDR_WIN_HI`, `spec/arch.md`), but
that window is programmed by the same software that issues the commands. Where isolation between software domains is
required, restrict the AXI master with the SoC's IOMMU or interconnect firewall, and set AxPROT accordingly
(`docs/INTEGRATION.md`).

## Reporting a vulnerability

Do not open a public issue for a security problem. Use GitHub's private vulnerability reporting (the "Report a
vulnerability" button on the repository's Security tab). Include the affected version or commit, a description of
the issue, and the steps or command stream that reproduce it.

Reports are acknowledged within 14 days. Once a fix is available, the issue is disclosed in the release notes
(`CHANGELOG.md`) with credit to the reporter unless they prefer otherwise.
