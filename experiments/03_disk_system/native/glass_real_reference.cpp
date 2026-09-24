#define main glass_port_main
#include "glass_disk_port.cpp"
#undef main
#include "glass/searcher/graph_searcher.hpp"

int main(int argc,char** argv) {
 try {
  if(argc!=7) throw std::runtime_error("usage: reference INDEX QUERY OUTPUT UNUSED WIDTHS THREADS");
  if(fs::exists(argv[3])) throw std::runtime_error("output exists");
  omp_set_num_threads(1);
  fs::path root=argv[1];auto meta=read_meta(root/"index.meta");
  glass::Graph<int32_t> graph(meta.n,meta.graph_k);graph.eps=meta.eps;
  std::ifstream g(root/"graph.pages",std::ios::binary),c(root/"sq4u_codes.pages",std::ios::binary);
  std::array<char,kPage> page{};
  for(std::size_t id=0;id<meta.n;++id) {
   if(id%meta.graph_records_per_page==0){g.read(page.data(),kPage);if(!g)throw std::runtime_error("truncated graph");}
   std::memcpy(graph.edges(id),page.data()+(id%meta.graph_records_per_page)*meta.graph_record_bytes,meta.graph_record_bytes);
  }
  glass::GraphSearcher<Quant> official(std::move(graph));
  official.nb=meta.n;official.d=meta.dim;official.quant=Quant(meta.dim);
  official.quant.calibrator.min=meta.cal_min;official.quant.calibrator.dif=meta.cal_dif;
  official.quant.storage.init(meta.n);
  if(official.quant.code_size()!=meta.code_size)throw std::runtime_error("code size mismatch");
  for(std::size_t id=0;id<meta.n;++id) {
   if(id%meta.code_records_per_page==0){c.read(page.data(),kPage);if(!c)throw std::runtime_error("truncated codes");}
   std::memcpy(official.quant.get_code(id),page.data()+(id%meta.code_records_per_page)*meta.code_size,meta.code_size);
  }
  auto queries=read_fvecs(argv[2]);if(queries.dim!=meta.dim)throw std::runtime_error("query dim mismatch");
  std::stringstream widths(argv[5]);std::string part;std::ofstream out(argv[3]);
  while(std::getline(widths,part,',')) {
   int width=std::stoi(part);if(width<10)throw std::runtime_error("width < topk");official.SetEf(width);
   for(std::size_t q=0;q<queries.rows;++q) {
    std::vector<int32_t> ids(kTopK);official.Search(queries.data.data()+q*queries.dim,kTopK,ids.data());
    out<<"{\"search_width\":"<<width<<",\"query_id\":"<<q<<",\"result_ids\":[";
    for(int k=0;k<kTopK;++k){if(k)out<<',';out<<ids[k];}out<<"]}\n";
   }
  }
  out.flush();if(!out)throw std::runtime_error("write failed");return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
