#include "/home/mr2/mr2-stack/rover/ros2_ws/src/mr2_video_streaming/src/video_control.hpp"
#include <iostream>
int main(){ for(int budget:{1500,3000})for(bool high:{false,true}){std::vector<video_control::Demand>d(5,{false,1800});d[0].high=high;const auto r=video_control::allocate(budget,d);std::cout<<budget<<" kbps, high="<<high<<":";for(int b:r)std::cout<<' '<<b;std::cout<<'\n';}}
