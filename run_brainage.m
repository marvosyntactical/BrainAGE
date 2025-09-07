
% Basename for the output .mat file
D.data    = 'brainage_export';

% Release or version information of the data
D.relnumber = '_CAT12.9';

% Char array of the group names
D.name_groups = char('D','F','FD','K');

% Index for each group
% Here we used a deviating order to show control subject as last group
D.ind_groups  = {1:14,15:39,40:50,51:80};

% % We almost always have to correct for the age-bias that leads to an understimation of 
% BrainAGE for young subjects and overstimation of elderly subjects. This 
% age-bias correction is estimated using the control subjects only and is apllied 
% to all data.
D.ind_adjust = 51:80;

% Use BrainAGE for trend correction (default linear trend)
D.trend_method = 1;

% Training data that are used for BrainAGE estimation
% In this example we combine 549 subjects from OASIS3, 547 from IXI,
% 651 from CamCan, 494 from SALD, and 516 from NKIe. This is also our
% default normative database for adults that covers a large age range
% (18..97 years)
% D.train_array = {'OASIS3_549+IXI547+CamCan651+SALD494+NKIe516'};
D.train_array = {};
D.n_regions = 1;
% D.age_test = 80;

% Age range of the training sample. The default is to use the complete age range.
D.age_range = [0 Inf];

% Be verbose
D.verbose = 1;

% Use resampling of 4 and 8mm
D.res_array    = char('8');  % resampling resolution

% Use smoothing of 4 and 8mm
D.smooth_array = char('s8'); % smoothing size

% Use rp1 and rp2 images (grey and white matter)
D.seg_array    = {'rp1','rp2'};

% This defines the ensemble method to combine the different models (e.g. 4/8mm resampling,
% 4/8mm smoothing, rp1/rp2). Here, we use 'Weighted Average', which averages
%  all models with weighting w.r.t. squared MAE.
D.ensemble = 5;        

% Call GPR method for estimating BrainAGE
% The estimated BrainAGE values are returned in the order you have defined
% using 'D.ind_groups' and also in the original (unsorted) order.
% Boxplots and some basic statistics are also provided during the estimation
% process.        
[BA, BA_unsorted] = BA_gpr_ui(D);

