# Script created by `sagex new script`.
# Edit it, then push it with:  sagex push script <this-file>
#
# PARAMETERS  (defined on the workflow's script node)
#   Each parameter is delivered to this script as an environment variable named
#   exactly as it is declared:
#     bash / shell :  $NAME   or  ${NAME}
#     powershell   :  $env:NAME
#   You can also write {{NAME}} inline in this file (double braces, case-insensitive);
#   Autosage substitutes the value before the script runs.
#   Parameter names must be letters/digits/underscore and not start with a digit
#   (e.g. SERVICE_NAME). Names that don't qualify are skipped.
#   Run on its own (sagex run script), the script asks only for the {{NAME}}
#   markers in this file, so write {{NAME}} for every input it needs.
#
# SECRETS  (parameters of type "password", or a value from a vault credential)
#   Delivered the same way, as an environment variable ($PASSWORD / $env:PASSWORD).
#   They are never placed on the command line, so they stay out of the process list.
#   Do NOT print or echo secrets: log masking is a safety net, not a guarantee.
#
# OUTPUT FOR LATER NODES
#   Print a JSON object to stdout, e.g.  {"status": "active", "exists": true}
#   Later nodes then read those fields as  {{this-node-id.output.status}}.
#   To use an upstream node's output here, set a parameter's value to
#   {{other-node-id.output.field}} and read that parameter as $NAME.
#
# EXIT CODE
#   Exit 0 means success; a non-zero exit marks this node failed and can stop the run.
#
# Write your code below.

