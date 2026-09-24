# Accuracy Adapters

Adapters in this directory document how each method maps its official 4-bit
quantizer into the shared accuracy schema.

Do not put ANN-search-only parameters, such as HNSW `efSearch`, into this
layer unless they are part of the method's own official quantization accuracy
test output.
