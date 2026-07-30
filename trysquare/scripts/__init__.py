"""Console entry points.

One module per installed command, named after the command it provides, so a
`[project.scripts]` line reads as what it is. The commands themselves live in
`trysquare.cli`; what belongs here is only the handful of concerns that appear the
moment a name is typed in a shell rather than a function called in Python: the exit
code, an interrupt, a closed pipe.
"""
