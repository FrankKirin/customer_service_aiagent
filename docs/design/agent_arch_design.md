### Agent架构

START
  |
prepare_node
  |
router_node(使用两种模式，如果用户问题有policy/manual关键词直接去policy或者manual，如果没有，就让LLM识别意图)
  |
general / policy / manual / clarify
  |
finally
  |
 END