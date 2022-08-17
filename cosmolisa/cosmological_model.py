#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys
import os
import ray
import time
import configparser
import subprocess
import numpy as np
import json
from optparse import OptionParser
from configparser import ConfigParser

# Import internal and external modules
from cosmolisa import readdata
from cosmolisa import plots
from cosmolisa import cosmology as cs
from cosmolisa import likelihood as lk
from cosmolisa import galaxy as gal
import cpnest.model

class CosmologicalModel(cpnest.model.Model):

    names  = [] #'h','om','ol','w0','w1']
    bounds = [] #[0.6,0.86],[0.04,0.5],[0.0,1.0],[-3.0,-0.3],[-1.0,1.0]]
    
    def __init__(self, model, data, corrections, *args, **kwargs):

        super(CosmologicalModel,self).__init__()
        # Set up the data
        self.data                = data
        self.N                   = len(self.data)
        self.model               = model.split('+')
        self.corrections         = corrections.split('+')
        self.truths              = kwargs['truths']
        self.em_selection        = kwargs['em_selection']
        self.z_threshold         = kwargs['z_threshold']
        self.snr_threshold       = kwargs['snr_threshold']
        self.event_class         = kwargs['event_class']
        self.dl_cutoff           = kwargs['dl_cutoff']
        self.sfr                 = kwargs['sfr']
        self.T                   = kwargs['T']
        self.magnitude_threshold = kwargs['m_threshold']
        self.O                   = None
        
        self.Mmin = -25.0
        self.Mmax = -15.0
        self.rate = 0
        self.luminosity = 0
        self.gw = 0
        self.cosmology = 0
        
        if ("LambdaCDM_h" in self.model):
            
            self.cosmology = 1
            self.npar      = 1
            self.names     = ['h']
            self.bounds    = [[0.6,0.86]]            

        if ("LambdaCDM_om" in self.model):
            
            self.cosmology = 1
            self.npar      = 1
            self.names     = ['om']
            self.bounds    = [[0.04,0.5]]

        if ("LambdaCDM" in self.model):
            
            self.cosmology = 1
            self.npar      = 2
            self.names     = ['h','om']
            self.bounds    = [[0.6,0.86],[0.04,0.5]]

        if ("CLambdaCDM" in self.model):
            
            self.cosmology = 1
            self.npar      = 3
            self.names     = ['h','om','ol']
            self.bounds    = [[0.6,0.86],[0.04,0.5],[0.0,1.0]]

        if ("LambdaCDMDE" in self.model):
            
            self.cosmology = 1
            self.npar      = 5
            self.names     = ['h','om','ol','w0','w1']
            self.bounds    = [[0.6,0.86],[0.04,0.5],[0.0,1.0],[-3.0,-0.3],[-1.0,1.0]]

        if ("DE" in self.model):
            
            self.cosmology = 1
            self.npar      = 2
            self.names     = ['w0','w1']
            self.bounds    = [[-3.0,-0.3],[-1.0,1.0]]

        if ("GW" in self.model):
            self.gw = 1
        else:
            self.gw = 0
        
        if ("Rate" in self.model):
#           e(z) = r0*(1.0+W)*exp(Q*z)/(exp(R*z)+W)
            self.rate = 1
            self.gw_correction = 1
            self.names.append('log10r0')
            self.bounds.append([-15,-8])
            self.names.append('W')
            self.bounds.append([0.0,300.0])
            self.names.append('Q')
            self.bounds.append([0.0,15.0])
            self.names.append('R')
            self.bounds.append([0.0,15.0])
            
        if ("Luminosity" in self.model):
        
            self.luminosity = 1
            self.em_correction = 1
            self.names.append('phistar0')
            self.bounds.append([1e-5,1e-1])
            self.names.append('phistar_exponent')
            self.bounds.append([-0.1,0.1])
            self.names.append('Mstar0')
            self.bounds.append([-22,-18])
            self.names.append('Mstar_exponent')
            self.bounds.append([-0.1,0.1])
            self.names.append('alpha0')
            self.bounds.append([-2.0,-1.0])
            self.names.append('alpha_exponent')
            self.bounds.append([-0.1,0.1])

        # if we are using GWs, add the relevant redshift parameters
        if self.gw == 1:
            for e in self.data:
                self.names.append('z%d'%e.ID)
                self.bounds.append([e.zmin,e.zmax])
        else:
            self.gw_redshifts = np.array([e.z_true for e in self.data])
        
        self._initialise_galaxy_hosts()
        
        if not("Rate" in self.model):
            if ("GW" in corrections):
                self.gw_correction = 1
            else:
                self.gw_correction = 0
        
        if not("Luminosity" in self.model):
            if ("EM" in corrections):
                self.em_correction = 1
            else:
                self.em_correction = 0
               
        print("\n====================================================================================================\n")
        print("CosmologicalModel model initialised with:")
        print(f"Event class: {self.event_class}")
        print(f"Analysis model: {self.model}")
        print(f"Number of events: {len(self.data)}")
        print(f"EM correction: {self.em_correction}")
        print(f"GW correction: {self.gw_correction}")
        print(f"Free parameters: {self.names}")
        print("\n====================================================================================================\n")
        print("Prior bounds:")
        for name,bound in zip(self.names, self.bounds):
            print(f"{str(name).ljust(4)}: {bound}")
        print("\n====================================================================================================\n")

    def _initialise_galaxy_hosts(self):
        self.hosts             = {e.ID:np.array([(g.redshift,g.dredshift,g.weight,g.magnitude) for g in e.potential_galaxy_hosts]) for e in self.data}
        self.galaxy_redshifts    = np.hstack([self.hosts[e.ID][:,0] for e in self.data]).copy(order='C')
        self.galaxy_magnitudes   = np.hstack([self.hosts[e.ID][:,3] for e in self.data]).copy(order='C')
        self.areas               = {e.ID:0.000405736691211125 * (87./e.snr)**2 for e in self.data}
        
    def log_prior(self,x):
    
        logP = super(CosmologicalModel,self).log_prior(x)
        
        if np.isfinite(logP):
            
            # check for the cosmological model
            if ("LambdaCDM_h" in self.model):
                self.O = cs.CosmologicalParameters(x['h'],self.truths['om'],self.truths['ol'],self.truths['w0'],self.truths['w1'])
            elif ("LambdaCDM_om" in self.model):
                self.O = cs.CosmologicalParameters(self.truths['h'],x['om'],1.0-x['om'],self.truths['w0'],self.truths['w1'])
            elif  ("LambdaCDM" in self.model):
                self.O = cs.CosmologicalParameters(x['h'],x['om'],1.0-x['om'],self.truths['w0'],self.truths['w1'])
            elif ("CLambdaCDM" in self.model):
                self.O = cs.CosmologicalParameters(x['h'],x['om'],x['ol'],self.truths['w0'],self.truths['w1'])
            elif ("LambdaCDMDE" in self.model):
                self.O = cs.CosmologicalParameters(x['h'],x['om'],x['ol'],x['w0'],x['w1'])
            elif ("DE" in self.model):
                self.O = cs.CosmologicalParameters(self.truths['h'],self.truths['om'],self.truths['ol'],x['w0'],x['w1'])
            else:
                self.O = cs.CosmologicalParameters(self.truths['h'],self.truths['om'],self.truths['ol'],self.truths['w0'],self.truths['w1'])
            # check for the rate model or GW corrections
            if ("Rate" in self.model):
                self.r0 = 10**x['log10r0']
                self.W  = x['W']
                self.Q  = x['Q']
                self.R  = x['R']
                if self.R <= self.Q:
                    # we want the merger rate to asymptotically either go to zero or to a finite number
                    self.O.DestroyCosmologicalParameters()
                    return -np.inf
            
            elif self.gw_correction == 1:
                self.r0 = self.truths['r0']
                self.W  = self.truths['W']
                self.Q  = self.truths['Q']
                self.R  = self.truths['R']
            # check for the luminosity model or EM corrections
            if ("Luminosity" in self.model):
                self.phistar0            = x['phistar0']
                self.phistar_exponent    = x['phistar_exponent']
                self.Mstar0              = x['Mstar0']
                self.Mstar_exponent      = x['Mstar_exponent']
                self.alpha0              = x['alpha0']
                self.alpha_exponent      = x['alpha_exponent']
            elif self.em_correction == 1:
                self.phistar0            = self.truths['phistar0']
                self.phistar_exponent    = self.truths['phistar_exponent']
                self.Mstar0              = self.truths['Mstar0']
                self.Mstar_exponent      = self.truths['Mstar_exponent']
                self.alpha0              = self.truths['alpha0']
                self.alpha_exponent      = self.truths['alpha_exponent']
        
        return logP

    def log_likelihood(self,x):
        
        logL_GW         = 0.0
        logL_rate       = 0.0
        logL_luminosity = 0.0
        
        # if we are looking at the luminosity function
        if self.luminosity == 1 and self.gw == 0:
            for e in self.data:
                Schecter = gal.GalaxyDistribution(self.O,
                                                  self.phistar0,
                                                  self.phistar_exponent,
                                                  self.Mstar0,
                                                  self.Mstar_exponent,
                                                  self.alpha0,
                                                  self.alpha_exponent,
                                                  self.Mmin,
                                                  self.Mmax,
                                                  e.zmin,
                                                  e.zmax,
                                                  0.0,
                                                  2.0*np.pi,
                                                  -0.5*np.pi,
                                                  0.5*np.pi,
                                                  self.magnitude_threshold,
                                                  self.areas[e.ID],
                                                  0,0,0)

                logL_luminosity += Schecter.loglikelihood(self.hosts[e.ID][:,3].copy(order='C'), self.hosts[e.ID][:,0].copy(order='C'))

            # if we do not care about GWs, return
            return logL_luminosity
        
        # if we are estimating the rate or we are correcting for GW selection effects, we need this part
        if self.rate == 1 or self.gw_correction == 1:
            Rtot    = lk.integrated_rate(self.r0, self.W, self.R, self.Q, self.O, 1e-5, self.z_threshold)
            Ntot    = Rtot*self.T
            
            # compute the probability of observing the events we observed
            Ndet = lk.gw_selection_probability_sfr(1e-5, self.z_threshold,
                                                   self.r0, self.W, self.R, self.Q,
                                                   self.snr_threshold, self.O)
            # compute the rate for the observed events
            # Rdet      = Rtot*selection_probability
            logL_rate = -Ndet+self.N*np.log(Ntot)
#            print(selection_probability, Rdet, Rtot, Ndet, Ntot, self.N)
            # if we do not care about GWs, compute the rate density at the known gw redshifts and return
            if self.gw == 0:
                return logL_rate+np.sum([lk.logLikelihood_single_event_rate_only(self.O, e.z_true, self.r0, self.W, self.R, self.Q, Ntot) for e in self.data])
            
        # if we are correcting for EM selection effects, we need this part
        if self.em_correction == 1:
        
            for j,e in enumerate(self.data):
                    
                    Sch = gal.GalaxyDistribution(self.O,
                                                 self.phistar0,
                                                 self.phistar_exponent,
                                                 self.Mstar0,
                                                 self.Mstar_exponent,
                                                 self.alpha0,
                                                 self.alpha_exponent,
                                                 self.Mmin,
                                                 self.Mmax,
                                                 e.zmin,
                                                 e.zmax,
                                                 0.0,
                                                 2.0*np.pi,
                                                 -0.5*np.pi,
                                                 0.5*np.pi,
                                                 self.magnitude_threshold,
                                                 self.areas[e.ID],
                                                 0,0,0)
                    
                    logL_GW += lk.logLikelihood_single_event_sel_fun(self.hosts[e.ID],
                                                                     e.dl,
                                                                     e.sigma,
                                                                     self.O,
                                                                     Sch,
                                                                     x['z%d'%e.ID],
                                                                     zmin = e.zmin,
                                                                     zmax = e.zmax)
                    if self.luminosity == 1:
                        logL_luminosity += Sch.loglikelihood(self.hosts[e.ID][:,3].copy(order='C'), self.hosts[e.ID][:,0].copy(order='C'))

        # we assume the catalog is complete and no correction is necessary
        else:
            logL_GW += np.sum([lk.logLikelihood_single_event(self.hosts[e.ID],
                                                             e.dl,
                                                             e.sigma,
                                                             self.O,
                                                             x['z%d'%e.ID],
                                                             zmin = self.bounds[self.npar+j][0],
                                                             zmax = self.bounds[self.npar+j][1])
                                                             for j,e in enumerate(self.data)])

        self.O.DestroyCosmologicalParameters()

        return logL_GW+logL_rate+logL_luminosity


#IMPROVEME: most of the options work only for EMRI and sBH. Extend to MBHB.
usage="""\n\n %prog --config-file config.ini\n
    ######################################################################################################################################################
    IMPORTANT: This code requires the installation of the CPNest branch 'massively_parallel': https://github.com/johnveitch/cpnest/tree/massively_parallel
    ######################################################################################################################################################

    #=======================#
    # Input parameters      #
    #=======================#

    'data'                        Default: ''.                                      Data location.
    'outdir'                      Default: './default_dir'.                         Directory for output.
    'event_class'                 Default: ''.                                      Class of the event(s) ['MBHB', 'EMRI', 'sBH'].
    'model'                       Default: ''.                                      Specify the cosmological model to assume for the analysis ['LambdaCDM', 'LambdaCDM_h', LambdaCDM_om, 'CLambdaCDM', 'LambdaCDMDE', 'DE'] and the type of analysis ['GW','Rate', 'Luminosity'] separated by a '+'.
    'truths'                      Default: {"h": 0.673, "om": 0.315, "ol": 0.685}.  Cosmology truths values.
    'corrections'                 Default: ''.                                      Family of corrections ('GW', 'EM') separated by a '+'
    'joint'                       Default: 0.                                       Run a joint analysis for N events, randomly selected.
    'zhorizon'                    Default: '1000.0'.                                Impose low-high cutoffs in redshift. It can be a single number (upper limit) or a string with z_min and z_max separated by a comma.
    'dl_cutoff'                   Default: -1.0.                                    Max EMRI dL(omega_true,zmax) allowed (in Mpc). This cutoff supersedes the zhorizon one.
    'z_event_sel'                 Default: 0.                                       Select N events ordered by redshift. If positive (negative), choose the X nearest (farthest) events.
    'one_host_sel'                Default: 0.                                       For each event, associate only the nearest-in-redshift host.
    'single_z_from_GW'            Default: 0.                                       Impose a single host for each GW having redshift equal to z_true. It works only if one_host_sel = 1.
    'equal_wj'                    Default: 0.                                       Impose all galaxy angular weights equal to 1.
    'event_ID_list'               Default: ''.                                      String of specific ID events to be read (separated by commas and without single/double quotation marks).
    'max_hosts'                   Default: 0.                                       Select events according to the allowed maximum number of hosts.
    'z_gal_cosmo'                 Default: 0.                                       If set to 1, read and use the cosmological redshift of the galaxies instead of the observed one.
    'snr_selection'               Default: 0.                                       Select N events according to SNR (if N>0 the N loudest, if N<0 the N faintest).
    'snr_threshold'               Default: 0.0.                                     Impose an SNR detection threshold X>0 (X<0) and select the events above (belove) X.
    'sigma_pv'                    Default: 0.0023.                                  Redshift error associated to peculiar velocity value (vp / c), used in the computation of the GW redshift uncertainty (0.0015 in https://arxiv.org/abs/1703.01300).
    'em_selection'                Default: 0.                                       Use an EM selection function.
    'split_data_num'              Default: 1.                                       Choose the number of parts into which to divide the list of events. Values: any integer number equal or greater than 2.
    'split_data_chunk'            Default: 0.                                       Choose which chunk of events to analyse. Only works if split_data_num > 1. Values: 1 up to split_data_num.
    'T'                           Default: 10.0.                                    Observation time (yr).
    'sfr'                         Default: 0.                                       Fit the star formation parameters too.
    'reduced_catalog'             Default: 0.                                       Select randomly only a fraction of the catalog (4 yrs of observation, hardcoded).
    'm_threshold'                 Default: 20.                                      Apparent magnitude threshold.
    'postprocess'                 Default: 0.                                       Run only the postprocessing. It works only with reduced_catalog=0.
    'screen_output'               Default: 0.                                       Print the output on screen or save it into a file.
    'verbose'                     Default: 2.                                       Sampler verbose.
    'maxmcmc'                     Default: 5000.                                    Maximum MCMC steps for MHS sampling chains.
    'nensemble'                   Default: 1.                                       Number of sampler threads using an ensemble sampler. Equal to the number of LP evolved at each NS step. It must be a positive multiple of nnest.
    'nslice'                      Default: 0.                                       Number of sampler threads using a slice sampler.
    'nhamiltonian'                Default: 0.                                       Number of sampler threads using a hamiltonian sampler.
    'nnest'                       Default: 1.                                       Number of parallel independent nested samplers.
    'nlive'                       Default: 1000.                                    Number of live points.
    'seed'                        Default: 0.                                       Random seed initialisation.
    'obj_store_mem'               Default: 2e9.                                     Amount of memory reserved for ray object store. Default: 2GB.
    'periodic_checkpoint_int'     Default: 21600.                                   Time interval between sampler checkpoint in seconds. Defaut: 21600 (6h).
    'resume'                      Default: 0.                                       If set to 1, resume a run reading the checkpoint files, otherwise run from scratch. Default: 0.

"""

def main():

    run_time = time.perf_counter()
    parser = OptionParser(usage)
    parser.add_option('--config-file', type='string', metavar = 'config_file', default = None)

    (opts,args) = parser.parse_args()
    config_file = opts.config_file

    if not(config_file):
        parser.print_help()
        parser.error('Please specify a config file.')
    if not(os.path.exists(config_file)):
        parser.error('Config file {} not found.'.format(config_file))
    Config = configparser.ConfigParser()
    Config.read(config_file)

    config_par = {
                'data'                      :  '',
                'outdir'                    :  './default_dir',
                'event_class'               :  '',
                'model'                     :  '',
                'truth_par'                 :  {"h": 0.673, "om": 0.315, "ol": 0.685},
                'corrections'               :  '',
                'joint'                     :  0,
                'zhorizon'                  :  '1000.0',
                'dl_cutoff'                 :  -1.0,
                'z_event_sel'               :  0,
                'one_host_sel'              :  0,
                'single_z_from_GW'          :  0,
                'equal_wj'                  :  0,
                'event_ID_list'             :  '',
                'max_hosts'                 :  0,
                'z_gal_cosmo'               :  0,
                'snr_selection'             :  0,
                'snr_threshold'             :  0.0,
                'sigma_pv'                  :  0.0023,
                'em_selection'              :  0,
                'split_data_num'            :  1,
                'split_data_chunk'          :  0,
                'T'                         :  10.,
                'sfr'                       :  0,
                'reduced_catalog'           :  0,
                'm_threshold'               :  20,
                'postprocess'               :  0,
                'screen_output'             :  0,    
                'verbose'                   :  2,
                'maxmcmc'                   :  5000,
                'nensemble'                 :  1,
                'nslice'                    :  0,
                'nhamiltonian'              :  0,
                'nnest'                     :  1,
                'nlive'                     :  1000,
                'seed'                      :  0,
                'obj_store_mem'             :  2e9,
                'periodic_checkpoint_int'   :  21600,
                'resume'                    :  0
                }

    for key in config_par:
        keytype = type(config_par[key])
        try: 
            if "truth_par" in key:
                config_par[key]=json.loads(Config.get("input parameters",'{}'.format(key)))
            else:
                config_par[key]=keytype(Config.get("input parameters",key))
        except (KeyError, configparser.NoOptionError, TypeError):
            pass

    try:
        outdir = str(config_par['outdir'])
    except(KeyError, ValueError):
        outdir = 'default_dir'
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    os.system('mkdir -p {}/CPNest'.format(outdir))
    os.system('mkdir -p {}/Plots'.format(outdir))
    #FIXME: avoid cp command when reading the config file from the outdir directory to avoid the 'same file' cp error
    os.system('cp {} {}/.'.format(opts.config_file, outdir))
    output_sampler = os.path.join(outdir,'CPNest')

    if not(config_par['screen_output']):
        if not(config_par['postprocess']):
            sys.stdout = open(os.path.join(outdir,'stdout.txt'), 'w')
            sys.stderr = open(os.path.join(outdir,'stderr.txt'), 'w')

    print("\n"+"Running cosmoLISA")
    print(f"cpnest installation version: {cpnest.__version__}")
    print(f"ray version: {ray.__version__}")
    print(f"cosmolisa likelihood version: {lk.__file__}")

    max_len_keyword = len('periodic_checkpoint_int')
    print((f"\nReading config file: {config_file}\n"))
    for key in config_par:
        print(("{name} : {value}".format(name=key.ljust(max_len_keyword), value=config_par[key])))

    truths = {'h': config_par['truth_par']['h'],
              'om': config_par['truth_par']['om'],
              'ol': config_par['truth_par']['ol'],
              'w0': -1.0,
              'w1': 0.0,
              'r0': 5e-10,
              'Q': 2.4,
              'W': 41.,
              'R': 5.2,
              'phistar0': 1e-2,
              'Mstar0': -20.7,
              'alpha0': -1.23,
              'phistar_exponent': 0.0,
              'Mstar_exponent': 0.0,
              'alpha_exponent': 0.0}

    print("\nTruths:")
    for key in truths:
        print(("{name} : {value}".format(name=key.ljust(max_len_keyword), value=truths[key])))
    print("")

    omega_true = cs.CosmologicalParameters(truths['h'],truths['om'],truths['ol'],truths['w0'],truths['w1'])

    formatting_string = "===================================================================================================="

    if (config_par['event_class'] == "MBHB"):
        # If running on MBHB, override the selection functions
        em_selection = 0
        events = readdata.read_MBHB_event(config_par['data'])

    if ((config_par['event_class'] == "EMRI") or (config_par['event_class'] == "sBH")):
        if (config_par['snr_selection'] != 0):
            events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, snr_selection=config_par['snr_selection'], sigma_pv=config_par['sigma_pv'], one_host_selection=config_par['one_host_sel'], z_gal_cosmo=config_par['z_gal_cosmo'])
        elif (config_par['z_event_sel'] != 0):
            events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, z_event_sel=config_par['z_event_sel'], one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])
        elif (config_par['dl_cutoff'] > 0) and (',' not in config_par['zhorizon']) and (config_par['zhorizon'] == '1000.0'):
            all_events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])
            events_selected = []
            print(f"\nSelecting events according to dl_cutoff={config_par['dl_cutoff']}:")
            for e in all_events:
                if (omega_true.LuminosityDistance(e.zmax) < config_par['dl_cutoff']):
                    events_selected.append(e)
                    print("Event {} selected: dl(z_max)={}.".format(str(e.ID).ljust(3), omega_true.LuminosityDistance(e.zmax)))
            events = sorted(events_selected, key=lambda x: getattr(x, 'dl'))
            print("\nSelected {} events from dl={} to dl={}:".format(len(events), events[0].dl, events[len(events)-1].dl))            
            for e in events:
                print("ID: {}  |  dl: {}".format(str(e.ID).ljust(3), str(e.dl).ljust(9)))     
        elif ((config_par['zhorizon'] != '1000.0') and (config_par['snr_threshold'] == 0.0)):
            events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, zhorizon=config_par['zhorizon'], one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])
        elif (config_par['max_hosts'] != 0):
            events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, max_hosts=config_par['max_hosts'], one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])
        elif (config_par['event_ID_list'] != ''):
            events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, event_ID_list=config_par['event_ID_list'], one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])
        elif (config_par['snr_threshold'] != 0.0):
            print(f"\nSelecting events according to snr_threshold={config_par['snr_threshold']}:")
            if not config_par['reduced_catalog']:
                events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, snr_threshold=config_par['snr_threshold'], one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])
            else:
                events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])
                # Draw a number of events in the 4-year scenario
                N = np.int(np.random.poisson(len(events)*4./10.))
                print(f"\nReduced number of events: {N}")
                selected_events = []
                k = 0
                while k < N and not(len(events) == 0):
                    idx = np.random.randint(len(events))
                    selected_event = events.pop(idx)
                    print("Drawn event {0}: ID={1} - SNR={2:.2f}".format(k+1, str(selected_event.ID).ljust(3), selected_event.snr))
                    if config_par['snr_threshold'] > 0.0:
                        if selected_event.snr > config_par['snr_threshold']:
                            print("Selected: ID={0} - SNR={1:.2f} > {2:.2f}".format(str(selected_event.ID).ljust(3), selected_event.snr, config_par['snr_threshold']))
                            selected_events.append(selected_event)
                        else: pass
                        k += 1
                    else:
                        if selected_event.snr < abs(config_par['snr_threshold']):
                            print("Selected: ID={0} - SNR={1:.2f} < {2:.2f}".format(str(selected_event.ID).ljust(3), selected_event.snr, config_par['snr_threshold']))
                            selected_events.append(selected_event)
                        else: pass
                        k += 1                        
                events = selected_events
                events = sorted(selected_events, key=lambda x: getattr(x, 'snr'))
                print("\nSelected {} events from SNR={} to SNR={}:".format(len(events), events[0].snr, events[len(events)-1].snr))
                for e in events:
                    print("ID: {}  |  dl: {}".format(str(e.ID).ljust(3), str(e.dl).ljust(9)))
        else:
            events = readdata.read_dark_siren_event(config_par['event_class'], config_par['data'], None, one_host_selection=config_par['one_host_sel'], sigma_pv=config_par['sigma_pv'], z_gal_cosmo=config_par['z_gal_cosmo'])

        if (config_par['joint'] != 0):
            N = joint
            if (N > len(events)):
                N = len(events)
                print(f"The catalog has a number of selected events smaller than the chosen number ({N}). Running on {len(events)}")
            events = np.random.choice(events, size = N, replace = False)
            print(formatting_string)
            print(f"Selecting a random catalog of {N} events for joint analysis:")
            print(formatting_string)
            if not(len(events) == 0):
                for e in events:
                    print("event {0}: distance {1} \pm {2} Mpc, z \in [{3},{4}] galaxies {5}".format(e.ID,e.dl,e.sigma,e.zmin,e.zmax,len(e.potential_galaxy_hosts)))
                print(formatting_string)
            else:
                print(f"None of the drawn events has z<{config_par['zhorizon']}. No data to analyse. Exiting.\n")
                exit()

    if (config_par['single_z_from_GW'] != 0) and (config_par['one_host_sel'] == 1):
        print("\nSimulating a single potential host with redshift equal to z_true.") 
        for e in events:
            e.potential_galaxy_hosts[0].redshift = e.z_true
            e.potential_galaxy_hosts[0].weight = 1.0

    if (config_par['equal_wj'] == 1):
        print("\nImposing all the galaxy angular weights equal to 1.")
        for e in events:
            for g in e.potential_galaxy_hosts:
                g.weight = 1.0

    if not (config_par['split_data_num'] <= 1):
        assert config_par['split_data_chunk'] <= config_par['split_data_num'], "Data split in {} chunks; chunk number {} has been chosen".format(config_par['split_data_num'], config_par['split_data_chunk'])
        events = sorted(events, key=lambda x: getattr(x, 'ID'))
        q, r = divmod(len(events), config_par['split_data_num'])
        split_events = list([events[i*q + min(i, r):(i+1)*q + min(i+1, r)] for i in range(config_par['split_data_num'])])
        print(f"\nInitial list of {len(events)} events split into {len(split_events)} chunks. \nChunk number {config_par['split_data_chunk']} is chosen.")
        events = split_events[config_par['split_data_chunk']-1]

    if (len(events) == 0):
        print("The passed catalog is empty. Exiting.\n")
        exit()

    print(f"\nDetailed list of the {len(events)} selected event(s):")
    print("\n"+formatting_string)
    if config_par['event_class'] == 'MBHB':
        events = sorted(events, key=lambda x: getattr(x, 'ID'))
        for e in events:
            print("ID: {}  |  z_host: {} |  dl: {} Mpc  |  sigmadl: {} Mpc  | hosts: {}".format(
            str(e.ID).ljust(3), str(e.potential_galaxy_hosts[0].redshift).ljust(8), 
            str(e.dl).ljust(9), str(e.sigma)[:6].ljust(7), str(len(e.potential_galaxy_hosts)).ljust(4)))
    else:
        events = sorted(events, key=lambda x: getattr(x, 'ID'))
        for e in events:
            print("ID: {}  |  SNR: {}  |  z_true: {} |  dl: {} Mpc  |  sigmadl: {} Mpc  |  hosts: {}".format(
            str(e.ID).ljust(3), str(e.snr).ljust(9), str(e.z_true).ljust(7), 
            str(e.dl).ljust(7), str(e.sigma)[:6].ljust(7), str(len(e.potential_galaxy_hosts)).ljust(4)))


    print(formatting_string+"\n")
    print("CPNest will be initialised with:")
    print(f"verbose:                 {config_par['verbose']}")
    print(f"nensemble:               {config_par['nensemble']}")
    print(f"nslice:                  {config_par['nslice']}")
    print(f"nhamiltonian:            {config_par['nhamiltonian']}")
    print(f"nnest:                   {config_par['nnest']}")
    print(f"nlive:                   {config_par['nlive']}")
    print(f"maxmcmc:                 {config_par['maxmcmc']}")
    print(f"object_store_memory:     {config_par['obj_store_mem']}")
    print(f"periodic_checkpoint_int: {config_par['periodic_checkpoint_int']}")
    print(f"resume:                  {config_par['resume']}")

    C = CosmologicalModel(model         = config_par['model'],
                          data          = events,
                          corrections   = config_par['corrections'],
                          truths        = truths,
                          em_selection  = config_par['em_selection'],
                          snr_threshold = config_par['snr_threshold'],
                          z_threshold   = float(config_par['zhorizon']),
                          event_class   = config_par['event_class'],
                          dl_cutoff     = config_par['dl_cutoff'],
                          sfr           = config_par['sfr'],
                          T             = config_par['T'],
                          m_threshold   = config_par['m_threshold']
                          )

    #IMPROVEME: postprocess doesn't work when events are randomly selected, since 'events' in C are different from the ones read from chain.txt
    if (config_par['postprocess'] == 0):
        # Each NS can be located in different processors, but all the subprocesses of each NS live on the same processor

        work=cpnest.CPNest(C,
                           verbose                      = config_par['verbose'],
                           maxmcmc                      = config_par['maxmcmc'],
                           nensemble                    = config_par['nensemble'],
                           nslice                       = config_par['nslice'],
                           nhamiltonian                 = config_par['nhamiltonian'],
                           nnest                        = config_par['nnest'],   
                           nlive                        = config_par['nlive'],  
                           object_store_memory          = config_par['obj_store_mem'],
                           output                       = output_sampler,
                           periodic_checkpoint_interval = config_par['periodic_checkpoint_int'],
                           resume                       = config_par['resume']
                           )

        work.run()
        print(f"log Evidence {work.logZ}")
        print("\n"+formatting_string+"\n")

        x = work.posterior_samples.ravel()

        ray.shutdown()
        # Save git info
        with open("{}/git_info.txt".format(outdir), "w+") as fileout:
            subprocess.call(["git", "diff"], stdout=fileout)
    else:
        print("Reading the .h5 file...")
        import h5py
        filename = os.path.join(outdir,'CPNest','cpnest.h5')
        h5_file = h5py.File(filename,'r')
        x = h5_file['combined'].get('posterior_samples')

    ############################################################################################################
    #######################################          MAKE PLOTS         ########################################
    ############################################################################################################

    if C.cosmology == 1:

        if ("LambdaCDM_h" in C.model):
            plots.histogram(x, model='LambdaCDM_h', truths=truths, outdir=outdir)
        elif ("LambdaCDM_om" in C.model):
            plots.histogram(x, model='LambdaCDM_om', truths=truths, outdir=outdir)
        elif ("LambdaCDM" in C.model):
            plots.corner_plot(x, model='LambdaCDM', truths=truths, outdir=outdir)
        elif ("CLambdaCDM" in C.model):
            plots.corner_plot(x, model='CLambdaCDM', truths=truths, outdir=outdir)
        elif ("LambdaCDMDE" in C.model):
            plots.corner_plot(x, model='LambdaCDMDE', truths=truths, outdir=outdir)
        elif ("DE" in C.model):
            plots.corner_plot(x, model='DE', truths=truths, outdir=outdir)

    if (((config_par['event_class'] == "EMRI") or (config_par['event_class'] == "sBH")) and (C.gw == 1)):
        for e in C.data:
            plots.redshift_ev_plot(x, model=C.model, event=e, em_sel=config_par['em_selection'], truths=truths, omega_true=omega_true, outdir=outdir)    
    elif (config_par['event_class'] == "MBHB"):
        plots.MBHB_regression(x, model=C.model, data=C.data, truths=truths, omega_true=omega_true, outdir=outdir)
    
    if ("Rate" in C.model):

        plots.corner_plot(x, model='Rate', truths=truths, outdir=outdir)
        plots.rate_plots(x, cosmo_model=C, truths=truths, omega_true=omega_true, outdir=outdir)

    if ("Luminosity" in C.model):

        plots.corner_plot(x, model='Luminosity', truths=truths, outdir=outdir)
        plots.luminosity_plots(x, cosmo_model=C, truths=truths, outdir=outdir)


    if (config_par['postprocess'] == 0):
        run_time = (time.perf_counter() - run_time)/60.0
        print('\nRun-time (min): {:.2f}\n'.format(run_time))


if __name__=='__main__':
    main()
