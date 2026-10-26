% P4 MATLAB Cross-Validation - BLAST-Lite LFP|Gr 独立验证
% 非交互式脚本, 从CSV读取SoC轨迹, 运行LFP|Gr老化模型, 输出Q(t)/r(t)
%
% 输入: soc_profile.csv (tsec, soc)
% 输出: blast_lfp_results.csv (t_days, EFC, q, qLossCal, qLossCyc, r, rGainCal, rGainCyc)

clear; clc;
fprintf('=== P4 BLAST-Lite LFP|Gr Cross-Validation ===\n');

% 路径设置
blast_root = 'D:\matlab\C\数据文献\BLAST-Lite-main\MATLAB BLAST-Lite';
addpath(genpath(fullfile(blast_root, 'Functions')));

% 加载LFP|Gr模型参数
fprintf('[1] 加载 LFP|Gr 模型参数...\n');
lifeMdl = func_LifeMdl_LoadParameters('LFP|Gr');
fprintf('  模型: %s, 状态数: %d, 输出数: %d\n', lifeMdl.model, lifeMdl.n_states, lifeMdl.n_outputs);

% 读取SoC轨迹 (从Python年度仿真导出)
fprintf('[2] 读取SoC轨迹...\n');
soc_csv = 'D:\matlab\C\07_results\annual\soc_profile_for_blast.csv';
cycle_data = readtable(soc_csv);
fprintf('  数据点: %d, tsec范围: [%.0f, %.0f]s, SoC范围: [%.3f, %.3f]\n', ...
    height(cycle_data), cycle_data.tsec(1), cycle_data.tsec(end), ...
    min(cycle_data.soc), max(cycle_data.soc));

% 重复1周SoC轨迹到1年 (BLAST-Lite按天处理)
week_length = cycle_data.tsec(end) - cycle_data.tsec(1);
weeks_per_year = 52;
cycle = repmat(cycle_data, weeks_per_year, 1);
% 重新生成时间戳
cycle.tsec = (0:height(cycle)-1)' * (cycle_data.tsec(2) - cycle_data.tsec(1));
fprintf('  扩展到1年: %d点, %.1f天\n', height(cycle), cycle.tsec(end)/(24*3600));

% 添加温度 (25C恒定)
cycle.TdegC = repmat(25.0, height(cycle), 1);

% 提取每日应力统计量
fprintf('[3] 提取每日应力统计量...\n');
dtdays = 1;
cycleLengthDays = round(cycle.tsec(end) / (24*3600));
stressors = zeros(ceil(cycleLengthDays/dtdays), 7);
for iDay = 1:dtdays:cycleLengthDays
    startIdx = max(1, find(cycle.tsec >= (iDay-1)*24*3600, 1));
    endIdx = find(cycle.tsec <= iDay*24*3600, 1, 'last');
    if isempty(endIdx), endIdx = height(cycle); end
    subcycle = struct();
    subcycle.tsec = cycle.tsec(startIdx:endIdx) - cycle.tsec(startIdx);
    subcycle.t = subcycle.tsec / (24*3600);
    subcycle.soc = cycle.soc(startIdx:endIdx);
    subcycle.TdegC = cycle.TdegC(startIdx:endIdx);
    subcycle = struct2table(subcycle);
    subcycle = func_LifeMdl_StressStatistics(subcycle);
    stressors(ceil(iDay/dtdays), :) = [subcycle.dt, subcycle.dEFC, subcycle.TdegK, ...
                                        subcycle.soc, subcycle.Ua, subcycle.dod, subcycle.Crate];
end
stressors = array2table(stressors, 'VariableNames', {'dt','dEFC','TdegK','soc','Ua','dod','Crate'});
fprintf('  应力统计: %d天, 平均DOD=%.3f, 平均SoC=%.3f, 平均Crate=%.4f\n', ...
    height(stressors), mean(stressors.dod), mean(stressors.soc), mean(stressors.Crate));

% 运行寿命仿真 (2年=730天)
fprintf('[4] 运行 LFP|Gr 寿命仿真 (2年)...\n');
tYears = 2;
lifeSim.t = [0:stressors.dt(1):(tYears*365)]';
lifeSim.EFC = zeros(size(lifeSim.t));
xx0 = zeros(lifeMdl.n_states, 1);
xx0(3) = 1e-8;
xx = zeros(lifeMdl.n_states, length(lifeSim.t));
yy = ones(lifeMdl.n_outputs, length(lifeSim.t));
yy([2,3,5,6], :) = 0;

steps_life_sim = length(lifeSim.t) - 1;
for j = 1:steps_life_sim
    idxRow = mod(j-1, size(stressors, 1)) + 1;
    lifeSim.EFC(j+1) = lifeSim.EFC(j) + stressors.dEFC(idxRow);
    xx(:,j+1) = func_LifeMdl_UpdateStates(lifeMdl, stressors(idxRow, :), xx0(:, 1));
    yy(:,j+1) = func_LifeMdl_UpdateOutput(lifeMdl, xx(:, j+1));
    xx0(:,1) = xx(:,j+1);
    if mod(j, 180) == 0
        fprintf('  Step %d/%d: q=%.4f, r=%.4f, EFC=%.1f\n', j, steps_life_sim, yy(1,j+1), yy(4,j+1), lifeSim.EFC(j+1));
    end
end

lifeSim.q = yy(1,:)';
lifeSim.qLossCal = yy(2,:)';
lifeSim.qLossCyc = yy(3,:)';
lifeSim.r = yy(4,:)';
lifeSim.rGainCal = yy(5,:)';
lifeSim.rGainCyc = yy(6,:)';

% 导出结果
fprintf('[5] 导出结果...\n');
out_csv = 'D:\matlab\C\07_results\annual\blast_lfp_results.csv';
out_data = [lifeSim.t, lifeSim.EFC, lifeSim.q, lifeSim.qLossCal, lifeSim.qLossCyc, ...
            lifeSim.r, lifeSim.rGainCal, lifeSim.rGainCyc];
out_header = {'t_days','EFC','q','qLossCal','qLossCyc','r','rGainCal','rGainCyc'};
T = array2table(out_data, 'VariableNames', out_header);
writetable(T, out_csv);
fprintf('  结果已保存: %s\n', out_csv);
fprintf('  最终: q=%.4f, r=%.4f, EFC=%.1f, t=%.0f天\n', ...
    lifeSim.q(end), lifeSim.r(end), lifeSim.EFC(end), lifeSim.t(end));

fprintf('\n=== BLAST-Lite LFP|Gr 交叉验证完成 ===\n');
