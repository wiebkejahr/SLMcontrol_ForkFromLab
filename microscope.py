'''
Project: deep-sted-autoalign
Created on: Tuesday, 7th January 2020 10:29:32 am
--------
@author: hmcgovern
'''


"""This file acquires a donut-esque image from the open Imspector window, gets its
predicted aberration coefficient weights by feeding it through a trained model, and
saves the important ones in a dictionary, which is passed to the GUI"""

import torch
import numpy as np
import argparse as ap
from skimage.transform import resize, rotate
import matplotlib.pyplot as plt
try:
    import specpy as sp
except:
    print("Specpy not installed!")
    pass
import sys
import time
sys.path.insert(1, 'autoalign/')
sys.path.insert(1, 'parameters/')
import utils.helpers as helpers
import utils.my_models as my_models
from scipy.ndimage.measurements import center_of_mass
from scipy.ndimage import shift
import torchvision.models as models
import tifffile

import slm_control.Pattern_Calculator as pc

import utils.vector_diffraction as vd


class Microscope():
    """ Class for simulating microscope acquisition for testing and when no
        microscope is available. Implements functions acquire data, 
        set and get stage / galvo offsets (for centering PSF),
        to calculate tiptilt and defocus
        and to simulate data acquisition using vector diffraction.
        Optical and numerical parameters used for simulations are currently 
        hard coded. Later: should be read in from config files.
        """
    def __init__(self, params_sim):
        super(Microscope, self).__init__()
        self.get_config()
        self.opt_props = params_sim["optical_params_sted"]
        self.num_props = params_sim["numerical_params"]
        # left handed circular
        self.polarization = [1.0/np.sqrt(2), 1.0/np.sqrt(2)*1j, 0]
        self.calc_data()

        
    def calc_data(self, mask_offset = [0,0], aberrs = np.zeros(11)):
        """ Simulates microscope acquisition using vector diffraction code.
            Inputs: array mask_offset: [x,y] shift of the phase mask, 
                    array aberrations: 11-dim vector, weights of Zernike modes
                    string mode: vortex to put on the phasemask (eg 2D, Gauss etc)
            Sets:   self.data: numpy array containing the simulated PSF
                    self.phasemask: image containing the phasemask
            Returns:self.data, 
                    self.phasemask
                    zerns: phasemask containing the aberrations
            """
        lp_scale_sted = vd.calc_lp(self.opt_props["P_laser"], 
                                   self.opt_props["rep_rate"], 
                                   self.opt_props["pulse_length"])
        size = np.asarray([self.num_props["inp_res"], self.num_props["inp_res"]])
        vortex = pc.create_donut(2*size, 0, 1, radscale = 2)
        self.zerns = pc.zern_sum(2*size, aberrs, self.num_props["orders"][3::], radscale = 2)
        self.phasemask = pc.crop(pc.add_images([vortex, self.zerns]), size, mask_offset)
        amp = np.ones_like(self.phasemask)
        
#        def correct_aberrations(size, ratios, orders, off = [0,0], radscale = 1):
        [xy, xz, yz, xyz] = vd.vector_diffraction(
            self.opt_props, self.num_props, 
            self.polarization, self.phasemask, amp, lp_scale_sted, plane='all', 
            offset=self.opt_props['offset'])
        self.data = np.stack((helpers.preprocess(xy), 
                              helpers.preprocess(xz), 
                              helpers.preprocess(yz)), axis = 0)
        
        return self.data, self.phasemask, self.zerns
        
        
    def get_config(self):
        self.config = {'stage_offsets' : [0,0,0]}
        
        
    def get_stage_offsets(self, mode = ''):
        xyz = self.config['stage_offsets']
        return xyz
    

    def set_stage_offsets(self, stage_offset = [0,0,0], mode = ''):
        self.config['stage_offsets'] = stage_offset

    def center_stage(self, img, xyz_init, px_size, mode = ''):
        """ defines center of the PSFs in img, then moves the stage accordingly
            TODO: img should be self.img?
            there's no need to loop it thru calling function."""
        d_xyz = helpers.get_CoMs(img) * px_size
        
        # TODO: doesn't work here bc not in loop
        # if CoM is more than 200 nm from center, skip and try againg
        # lim = 160e-9
        # if np.abs(d_xyz[0]) >= lim or np.abs(d_xyz[1]) >= lim or np.abs(d_xyz[2])>=lim:
        #     print('skipped', d_xyz)
        #     self.set_stage_offsets(xyz_init, mode)
        #     continue

        # 3. centers using ImSpector
        xyz_0 = self.get_stage_offsets(mode)
        xyz_Pos = xyz_0 - d_xyz 
        # TODO: test again if this works. if overall drift has been more then 800 um, reset.
        # if np.abs(xPos) >= 800e-6 or np.abs(yPos) >= 800e-6:
        #     print('skipped', xPos, yPos)
        #     scope.set_stage_offsets(xyz_init, 'fine')
        #     continue

        # write new position values
        self.set_stage_offsets(xyz_Pos)
        
        
    # def correct_tip_tilt(self):
    #     return helpers.calc_tip_tilt(self.data[0])
        
    
    # def correct_defocus(self):
    #     return helpers.calc_defocus(self.data[1], self.data[2])
    
        
    def acquire_image(self, multi = True, mask_offset = [0,0], aberrs = np.zeros(11)):
        print("acquiring with: ", mask_offset, aberrs)
        self.calc_data(mask_offset = mask_offset, aberrs = aberrs)            
        if multi:
            img = np.stack((self.data[0], self.data[1], self.data[2]), axis=0)
        else:
            img = self.data[0]
        stats = [np.max(self.data[0]), np.min(self.data[0]), np.std(self.data[0])]
        return img, stats
    
    
    def save_img(self, path):
        tifffile.imsave(path + '.tif', self.data)
    

class Abberior():
    """bla"""
    def __init__(self, params_sim):
        #super(Microscope, self).__init__(self)
        self.get_config()
        
    def get_config(self):
        """" initializes Abberior Imspector instance and config"""
        self.gui = sp.Imspector()
        self.msr_names = self.gui.measurement_names()
        self.msr = self.gui.active_measurement()
        self.config = self.msr.active_configuration()
        #print(self.config.parameters('prop_version'))
        
    def get_stage_offsets(self, mode = 'fine'):
        """ gets offsets from abberior gui, either for galvo or for the stage
            depending whether mode == 'fine' or mode == 'corase'."""
        c = self.gui.active_measurement().active_configuration()
        if mode == 'fine':
            x = c.parameters('ExpControl/scan/range/x/g_off')
            y = c.parameters('ExpControl/scan/range/y/g_off')
            z = c.parameters('ExpControl/scan/range/z/g_off')
        elif mode == 'coarse':
            x = c.parameters('ExpControl/scan/range/offsets/coarse/x/g_off')
            y = c.parameters('ExpControl/scan/range/offsets/coarse/y/g_off')
            z = c.parameters('ExpControl/scan/range/offsets/coarse/z/g_off')
        return [x, y, z]
    
    
    def set_stage_offsets(self, stage_offsets = [0,0,0], mode = 'fine'):
        """ sets offsets in abberior gui, either for galvo or for the stage
            depending whether mode == 'fine' or mode == 'coarse'."""
        c = self.gui.active_measurement().active_configuration()
        if mode == 'fine':
            c.set_parameters('ExpControl/scan/range/x/g_off', stage_offsets[0])
            c.set_parameters('ExpControl/scan/range/y/g_off', stage_offsets[1])
            c.set_parameters('ExpControl/scan/range/z/g_off', stage_offsets[2])
        elif mode == 'coarse':
            c.set_parameters('ExpControl/scan/range/offsets/coarse/x/g_off', stage_offsets[0])
            c.set_parameters('ExpControl/scan/range/offsets/coarse/y/g_off', stage_offsets[1])
            c.set_parameters('ExpControl/scan/range/offsets/coarse/y/g_off', stage_offsets[2])

    def center_stage(self, img, xyz_init, px_size, mode = ''):
        """ defines center of the PSFs in img, then moves the stage accordingly
            TODO: img should be self.img?
            there's no need to loop it thru calling function."""
        d_xyz = helpers.get_CoMs(img) * px_size
        
        # TODO: doesn't work here bc not in loop
        # if CoM is more than 200 nm from center, skip and try againg
        # lim = 160e-9
        # if np.abs(d_xyz[0]) >= lim or np.abs(d_xyz[1]) >= lim or np.abs(d_xyz[2])>=lim:
        #     print('skipped', d_xyz)
        #     self.set_stage_offsets(xyz_init, mode)
        #     continue

        # 3. centers using ImSpector
        xyz_0 = self.get_stage_offsets(mode)
        xyz_Pos = xyz_0 - d_xyz 
        # TODO: test again if this works. if overall drift has been more then 800 um, reset.
        # if np.abs(xPos) >= 800e-6 or np.abs(yPos) >= 800e-6:
        #     print('skipped', xPos, yPos)
        #     scope.set_stage_offsets(xyz_init, 'fine')
        #     continue

        # write new position values
        print('centering: ', d_xyz/px_size, d_xyz, xyz_0, xyz_Pos)
        self.set_stage_offsets(xyz_Pos)
    
    def grab_image(self, msr, window = 'ExpControl Ch1 {1}'):
        #TODO: seems is only used internally
        # if not multi: grabs the latest image from Imspector without acquiring
        # for xy models: assumption that acquisition is running constantly
        # grabs measurment setup, stats etc
        msr = self.gui.active_measurement()
        try:            
            self.gui.start(msr)
            time.sleep(3)
            self.gui.pause(msr)
            img = msr.stack(window).data() # converts it to a numpy array
            stats = [np.max(img), np.min(img), np.std(img)]
            # takes off black edge, resizes to (64, 64) and standardizes
            img_p = helpers.preprocess(img)
            time.sleep(0.5)
        except:
            print("Cannot find ", window, " window")
        #         exit()
        return img_p, stats
    
    
    def acquire_image(self, multi=False, mask_offset = [0,0], aberrs = np.zeros(11)):
        """" Acquires xy, xz and yz images in Imspector, returns some stats 
            and the active configurations.
            Configuration names and measurements names are hard coded
        """
        # acquires xy view
        msr = self.gui.measurement(self.gui.measurement_names()[0])
        self.gui.activate(msr)
        try:
            msr.activate(msr.configuration('xy2d'))
            image_xy, stats = self.grab_image(msr, window = 'ExpControl Ch1 {1}')
        except:
            print("cannot find xy2d config or 'ExpControl Ch1 {1}' window")
            exit()
    
        if multi:
            # acquires the other two views and stacks them
            try:
                msr.activate(msr.configuration('xz2d'))
                image_xz, _ = self.grab_image(msr, window = 'ExpControl Ch1 {13}')
            except:
                print("cannot find xz2d config or 'ExpControl Ch1 {13}' window")
                exit()
            try:
                msr.activate(msr.configuration('yz2d'))
                image_yz, _ = self.grab_image(msr, window = 'ExpControl Ch1 {15}')
            except:
                print("cannot find yz2d config or 'ExpControl Ch1 {15}' window")  
                exit()
            image = np.stack((image_xy, image_xz, image_yz), axis=0)
        else:
            image = image_xy
        
        return image, stats
    
    
    def save_img(self, path):
        msr = self.gui.active_measurement()
        msr.save_as(path + '.msr')
    
    

def abberior_predict(model_store_path, model_def, image, ii=1):
    
    # best_coeffs = []
    best_corr = 0
    for _ in range(ii):

        if model_def["multi_flag"]:
            in_dim = 3
        else:
            in_dim = 1

        out_dim = 0
        if model_def["zern_flag"]: out_dim += 11
        if model_def["offset_flag"]: out_dim += 2
        
        
        resolution = model_def["resolution"]
        print('in dim', in_dim, 'out dim', out_dim, 'res', resolution)


        model = my_models.TheUltimateModel(input_dim=in_dim, output_dim=out_dim, res=resolution, concat=model_def["concat"])
        # from torchsummary import summary
        # print(summary(model, input_size=(in_dim, resolution, resolution), batch_size=out_dim))
        # gets preds
        #print("predict: ", model_store_path)
        checkpoint = torch.load(model_store_path)
        model.load_state_dict(state_dict=checkpoint['model_state_dict'])
        #model = torch.load(model_store_path)

        # Test the model
        model.eval()

        with torch.no_grad():
            # adds 3rd color channel dim and batch dim
            if model_def["multi_flag"]:   
                input_image = torch.from_numpy(image).unsqueeze(0)
            else:
                # NOTE: THIS IS ONLY FOR 1D
                input_image = torch.from_numpy(image).unsqueeze(0).unsqueeze(0)
    
            outputs = model(input_image.float())
            # coeffs = outputs.numpy().squeeze()

        if len(outputs.numpy()[0]) == 13:
            zern_label = outputs.numpy()[0][:-2]
            offset_label = outputs.numpy()[0][-2:]

        elif len(outputs.numpy()[0])== 11:
            zern_label = outputs.numpy()[0]
            offset_label = [0,0]
        
        elif len(outputs.numpy()[0])== 2:
            zern_label = np.asarray([0.0*11])
            offset_label = outputs.numpy()[0]
                    
        # zern = coeffs
        # offset_label = np.asarray([0,0])
        
        # return coeffs
        # if offset:
        #     zern = coeffs[:-2]
        #     offset_label = coeffs[-2:]
        # else:
        #     zern = coeffs
        #     offset_label = [0,0]
        reconstructed = helpers.get_sted_psf(coeffs=zern_label, offset_label=offset_label, multi=model_def["multi_flag"], defocus=False)
        corr = helpers.corr_coeff(image, reconstructed)
        if corr > best_corr:
            best_corr = corr
            best_coeffs = zern_label
            best_offsets = offset_label

    # if offset:
    #     zern = best_coeffs[:-2]
    #     offset_label = best_coeffs[-2:]
    # else:
    #     zern = best_coeffs 
    #     offset_label = [0,0]   
   
    

    return np.asarray(best_coeffs), np.asarray(best_offsets)




if __name__ == "__main__":
    #abberior_multi('models/20.05.18_scaling_fix_eps_15_lr_0.001_bs_64_2.pth')
    #aberrs = np.random.random(11)
    aberrs = np.zeros(11)
    aberrs[0] = 0.5
    mask_offset = [10,5]
    
    scope = Microscope()
    plt.figure()
    plt.subplot(2,5,1)
    plt.imshow(scope.data[0])
    plt.subplot(2,5,2)
    plt.imshow(scope.data[1])
    plt.subplot(2,5,3)
    plt.imshow(scope.data[2])
    plt.subplot(2,5,4)
    plt.imshow(scope.phasemask)
    plt.subplot(2,5,5)
    plt.imshow(scope.zerns)
    
    scope.acquire_image(multi=True, mask_offset = mask_offset, aberrs = aberrs)
    plt.subplot(2,5,6)
    plt.imshow(scope.data[0])
    plt.subplot(2,5,7)
    plt.imshow(scope.data[1])
    plt.subplot(2,5,8)
    plt.imshow(scope.data[2])
    plt.subplot(2,5,9)
    plt.imshow(scope.phasemask)    
    plt.subplot(2,5,10)
    plt.imshow(scope.zerns)
    
    
    
    