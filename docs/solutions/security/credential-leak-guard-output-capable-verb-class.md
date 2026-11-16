# Historical decision: retire the command-string credential parser

The former credential-leak guard attempted to classify shell reads through a
large regex and dataflow parser.  Security review found that shell syntax,
recursive interpreters, and previously unseen readers made that approach
unreviewable as a credential boundary.

It is no longer an installed hook or a runnable test.  The active design is
the fixed-profile executor and small structural hook described in
[managed credential boundary](managed-credential-boundary.md).  This file is
only the decision record retained for repository history; it is not an
executable specification or a supported policy.
