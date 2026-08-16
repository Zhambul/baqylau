"""The activity mirror's rendering pipeline.

One direction, four steps: `visibility` decides which canonical activity the
mirror shows, `presenter` turns it into the drawing model in `blocks`,
`highlight` formats commands and source inside it, and `renderer` paints the
blocks into a live pane, replacing and reflowing what is already on screen.
"""
