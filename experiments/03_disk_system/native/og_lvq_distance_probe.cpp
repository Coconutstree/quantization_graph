#define main og_port_main
#include "og_lvq_disk_port.cpp"
#undef main
int main(int argc,char**argv) {
 if(argc!=4 && argc!=5 && argc!=7)return 2;
 fs::path root=argv[1];auto m=read_meta(root/"index.meta");auto c=read_centroids(root/"centroids.f32");auto q=read_fvecs(argv[2]);
 if(argc==7){
  // Diagnostic copy of search_one's queue operations; only distance source changes.
  std::size_t qi=std::stoull(argv[5]);int width=std::stoi(argv[6]);
  if(qi>=q.rows || width<10)return 6;
  std::vector<float> oracle(m.n);std::ifstream in(argv[4],std::ios::binary);
  in.read(reinterpret_cast<char*>(oracle.data()),oracle.size()*sizeof(float));if(!in)return 7;
  QueryStats stats;DiskRows rows(root,m,&stats);
  svs::index::vamana::SearchBuffer<std::uint32_t,std::less<>> pool(
   static_cast<std::size_t>(std::max(width,kTopK)),std::less<>{},false);
  pool.push_back(svs::SearchNeighbor<std::uint32_t>{m.entry_point,oracle.at(m.entry_point)});pool.sort();
  std::ofstream trace(std::string(argv[3])+".trace.csv");
  while(!pool.done()){
   auto u=pool.next().id();trace<<u<<'\n';auto grow=rows.graph(u);
   auto degree=std::min<std::size_t>(grow.front(),m.degree);
   for(std::size_t i=0;i<degree;++i){auto v=grow[i+1];if(v>=m.n)return 8;
    if(pool.emplace_visited(v))continue;
    pool.insert(svs::SearchNeighbor<std::uint32_t>{v,oracle.at(v)});
   }
  }
  std::ofstream out(argv[3]);out<<width<<','<<qi;
  for(std::size_t i=0;i<pool.size() && i<kTopK;++i)out<<','<<pool[i].id();out<<'\n';
  return out && trace?0:4;
 }
 if(argc==5 && std::string(argv[4])=="search") {
  std::ofstream out(argv[3]);
  for(int width:{10,20,40,60,100,160,240,400,580})for(std::size_t qi=0;qi<q.rows;++qi){
   QueryStats stats;DiskRows rows(root,m,&stats);
   auto ids=search_one(rows,c,q.data.data()+qi*m.dim,width,stats);
   out<<width<<','<<qi;
   for(auto id:ids)out<<','<<id;
   out<<'\n';
  }
  return out?0:4;
 }
 std::ifstream rows(root/"lvq4.pages",std::ios::binary);std::vector<unsigned char> row(m.lvq_record_bytes);
 std::ofstream out(argv[3]);out<<std::setprecision(17);
 if(argc==5) {
  std::ifstream requests(argv[4]);if(!requests)return 5;
  std::size_t qi,id;
  while(requests>>qi>>id){
   if(qi>=q.rows || id>=m.n)return 6;
   rows.seekg((id/m.lvq_records_per_page)*kPage+(id%m.lvq_records_per_page)*m.lvq_record_bytes);
   rows.read(reinterpret_cast<char*>(row.data()),row.size());if(!rows)return 3;
   out<<qi<<','<<id<<','<<lvq_distance(row.data(),m,c,q.data.data()+qi*m.dim)<<'\n';
  }
  return requests.eof() && out?0:4;
 }
 for(std::size_t qi=0;qi<std::min<std::size_t>(q.rows,16);++qi)for(std::size_t j=0;j<64;++j){
  std::size_t id=(j*7919+qi*17)%m.n;
  rows.seekg((id/m.lvq_records_per_page)*kPage+(id%m.lvq_records_per_page)*m.lvq_record_bytes);
  rows.read(reinterpret_cast<char*>(row.data()),row.size());if(!rows)return 3;
  out<<qi<<','<<id<<','<<lvq_distance(row.data(),m,c,q.data.data()+qi*m.dim)<<'\n';
 }
 return out?0:4;
}
